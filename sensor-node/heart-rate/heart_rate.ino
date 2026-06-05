#include <Wire.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "MAX30105.h"
#include "heartRate.h"
#include "config.h"

MAX30105 sensor;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

const byte RATE_SIZE = 4;
byte rates[RATE_SIZE];
byte rateSpot = 0;
long lastBeat = 0;
float bpm = 0;
int avgBpm = 0;
unsigned long lastPublish = 0;

void mqttConnect() {
  while (!mqtt.connected()) {
    mqtt.connect("esp32-heartrate");
    delay(500);
  }
}

void setup() {
  Serial.begin(115200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("WiFi OK");

  mqtt.setServer(MQTT_HOST, MQTT_PORT);

  Wire.begin(SDA_PIN, SCL_PIN);
  if (!sensor.begin(Wire, I2C_SPEED_FAST)) {
    Serial.println("MAX30102 not found — check wiring");
    while (1);
  }
  sensor.setup();
  sensor.setPulseAmplitudeRed(0x0A);
  sensor.setPulseAmplitudeGreen(0);
  Serial.println("MAX30102 ready");
}

void loop() {
  long irValue = sensor.getIR();

  if (checkForBeat(irValue)) {
    long delta = millis() - lastBeat;
    lastBeat = millis();
    bpm = 60.0 / (delta / 1000.0);

    if (bpm > 20 && bpm < 255) {
      rates[rateSpot++] = (byte)bpm;
      rateSpot %= RATE_SIZE;
      avgBpm = 0;
      for (byte i = 0; i < RATE_SIZE; i++) avgBpm += rates[i];
      avgBpm /= RATE_SIZE;
    }
  }

  // Only publish when finger is detected (IR > 50000) and interval passed
  if (millis() - lastPublish > PUBLISH_INTERVAL && irValue > 50000 && avgBpm > 0) {
    mqttConnect();
    char payload[64];
    snprintf(payload, sizeof(payload), "{\"bpm\":%d}", avgBpm);
    mqtt.publish(MQTT_TOPIC, payload);
    Serial.println(payload);
    lastPublish = millis();
  }

  if (irValue < 50000) Serial.print(".");  // no finger

  mqtt.loop();
}
