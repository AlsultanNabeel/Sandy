// =========================
// ESP32-CAM — Sandy's Vision (MQTT-based)
// =========================
// شيلنا Arduino IoT Cloud (سبب الانقطاع المتكرر) — الآن:
//   • WiFi مباشر
//   • MQTT (HiveMQ) — نفس البروكر تبع Sandy
//   • Topics:
//       sandy/cam/request   ← Sandy تطلب snapshot
//       sandy/cam/snapshot  ← ESP-CAM ينشر chunks الصورة
//       sandy/cam/status    ← حالة الكاميرا كل 10s
//       sandy/cam/event     ← أحداث (e.g. capture errors)
//   • OTA + Telnet — لا حاجة لـ TTL بعد الآن
//
// التقسيم على ملفات .ino — يدمجها Arduino IDE تلقائياً:
//   esp32cam_Camera.ino — globals + setup + loop (هذا الملف)
//   cam_capture.ino     — esp_camera init + JPEG capture + chunked publish
//   cam_mqtt.ino        — MQTT connect / subscribe / status / send chunks
//   cam_ota.ino         — OTA + Telnet
//   cam_wifi.ino        — WiFi + diagnostics

#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoOTA.h>
#include <PubSubClient.h>
#include "esp_system.h"
#include "config.h"
#include "secrets.h"

// ── Telnet mirror (Serial → WiFi) ───────────────────────────────
WiFiServer g_telnetServer(23);
WiFiClient g_telnetClient;

class MirrorStream : public Print {
 public:
  size_t write(uint8_t c) override {
    Serial.write(c);
    if (g_telnetClient && g_telnetClient.connected()) g_telnetClient.write(c);
    return 1;
  }
  size_t write(const uint8_t* buf, size_t n) override {
    Serial.write(buf, n);
    if (g_telnetClient && g_telnetClient.connected()) g_telnetClient.write(buf, n);
    return n;
  }
};
MirrorStream g_log;

// ── Cross-file state ────────────────────────────────────────────
bool g_networkServicesStarted = false;
bool g_cameraReady = false;
bool g_snapshotPending = false;          // طلب snapshot قيد التنفيذ
String g_currentRequestId = "";          // UUID من Sandy backend
unsigned long g_lastStatusPubMs = 0;

void setup() {
  Serial.begin(CAMERA_SERIAL_BAUD);
  delay(CAMERA_BOOT_DELAY_MS);
  Serial.println("\n[BOOT] ESP32-CAM starting  build=v3-dispatch-log");

  WiFi.onEvent(onWiFiEvent);
  connectWiFi();

  // ابدأ تهيئة الكاميرا — لو فشل، نعيد عند أول طلب snapshot
  setupCamera();
}

void loop() {
  ensureWiFiConnected();
  startNetworkServicesIfReady();

  if (g_networkServicesStarted) {
    ArduinoOTA.handle();
    updateTelnet();
    updateMQTT();
  }

  // طلب snapshot في انتظار المعالجة — نلتقطه وننشره
  if (g_snapshotPending) {
    g_snapshotPending = false;
    Serial.printf("[LOOP] dispatching capture for id=%s\n", g_currentRequestId.c_str());
    Serial.flush();
    captureAndPublishSnapshot(g_currentRequestId);
    Serial.println("[LOOP] capture call returned");
    Serial.flush();
  }

  delay(1);  // yield للـ TCP/WiFi stacks
}
