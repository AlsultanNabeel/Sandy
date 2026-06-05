#include <BLEDevice.h>
#include <BLEScan.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "config.h"

BLEScan* pBLEScan;
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

bool lastPresence = false;
unsigned long lastSeenTime = 0;

class PhoneScanner : public BLEAdvertisedDeviceCallbacks {
  void onResult(BLEAdvertisedDevice device) {
    String addr = device.getAddress().toString().c_str();
    addr.toUpperCase();
    if (addr == TARGET_MAC) lastSeenTime = millis();
  }
};

void mqttConnect() {
  while (!mqtt.connected()) {
    mqtt.connect("esp32-presence");
    delay(500);
  }
}

void publish(const char* state) {
  mqttConnect();
  mqtt.publish(MQTT_TOPIC, state, true);
  Serial.println(state);
}

void setup() {
  Serial.begin(115200);

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  Serial.println("WiFi OK");

  mqtt.setServer(MQTT_HOST, MQTT_PORT);

  BLEDevice::init("");
  pBLEScan = BLEDevice::getScan();
  pBLEScan->setAdvertisedDeviceCallbacks(new PhoneScanner());
  pBLEScan->setActiveScan(true);
  pBLEScan->setInterval(100);
  pBLEScan->setWindow(99);
}

void loop() {
  pBLEScan->start(3, false);
  pBLEScan->clearResults();

  bool present = (millis() - lastSeenTime) < AWAY_TIMEOUT_MS;

  if (present != lastPresence) {
    publish(present ? "home" : "away");
    lastPresence = present;
  }

  mqtt.loop();
  delay(1000);
}
