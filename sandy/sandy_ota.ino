// =========================
// Sandy — OTA + Telnet
// =========================
// OTA:    رفع الكود عبر WiFi المحلي — Arduino IDE تشوف ESP كـ Network Port
// Telnet: قراءة الـ Serial عبر WiFi (port 23) — اتصل بـ `nc 192.168.x.x 23`
// كلاهما **محلي بحت** — لا broker، لا internet
#include "esp_task_wdt.h"
#define SANDY_OTA_HOSTNAME  "sandy-esp32"
// SANDY_OTA_PASSWORD يأتي من secrets.h (مهمل من git)

// g_networkServicesStarted مُعرَّف في sandy.ino (يستخدمه أكتر من ملف)

static void setupOTA() {
  ArduinoOTA.setHostname(SANDY_OTA_HOSTNAME);
  ArduinoOTA.setPassword(SANDY_OTA_PASSWORD);

  ArduinoOTA.onStart([]() {
    g_log.println("[OTA] update starting — stopping motors/buzzer + WDT");
    stopMotors();
    stopBuzzer();
    esp_task_wdt_delete(NULL);  // ألغي تسجيل المهمة الحالية من الـ WDT
  });
  ArduinoOTA.onEnd([]() {
    g_log.println("[OTA] update complete — rebooting");
  });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    static unsigned int lastTen = 999;
    unsigned int ten = (progress * 10) / total;
    if (ten != lastTen) {
      lastTen = ten;
      g_log.printf("[OTA] %u%%\n", ten * 10);
    }
  });
  ArduinoOTA.onError([](ota_error_t error) {
    g_log.printf("[OTA] error %u\n", (unsigned)error);
  });

  ArduinoOTA.begin();
  g_log.printf("[OTA] ready as '%s' @ %s\n",
               SANDY_OTA_HOSTNAME, WiFi.localIP().toString().c_str());
}

static void setupTelnet() {
  g_telnetServer.begin();
  g_telnetServer.setNoDelay(true);
  g_log.printf("[TELNET] server on port 23 — connect via: nc %s 23\n",
               WiFi.localIP().toString().c_str());
}

void updateTelnet() {
  // قبول client جديد لو ما في حالياً
  if (g_telnetServer.hasClient()) {
    if (g_telnetClient && g_telnetClient.connected()) {
      // في client متصل — ارفض الجديد
      WiFiClient newClient = g_telnetServer.available();
      newClient.println("[TELNET] busy — only one client allowed");
      newClient.stop();
    } else {
      g_telnetClient = g_telnetServer.available();
      g_telnetClient.println("=== Sandy ESP32 Serial mirror ===");
    }
  }
  // امسح الـ buffer للـ client لو فصل
  if (g_telnetClient && !g_telnetClient.connected()) {
    g_telnetClient.stop();
  }
}

void startNetworkServicesIfReady() {
  if (g_networkServicesStarted) return;
  if (WiFi.status() != WL_CONNECTED) return;
  setupOTA();
  setupTelnet();
  setupMQTT();
  g_networkServicesStarted = true;
}
