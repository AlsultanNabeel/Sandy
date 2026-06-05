// =========================
// ESP32-CAM — OTA + Telnet
// =========================

void setupOTA() {
  ArduinoOTA.setHostname(SANDY_OTA_HOSTNAME);
  ArduinoOTA.setPassword(SANDY_OTA_PASSWORD);
  ArduinoOTA.onStart([]() {
    g_log.println("[OTA] update starting");
  });
  ArduinoOTA.onEnd([]() {
    g_log.println("[OTA] complete — rebooting");
  });
  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    static unsigned int lastTen = 999;
    unsigned int ten = (progress * 10) / total;
    if (ten != lastTen) { lastTen = ten; g_log.printf("[OTA] %u%%\n", ten * 10); }
  });
  ArduinoOTA.onError([](ota_error_t error) {
    g_log.printf("[OTA] error %u\n", (unsigned)error);
  });
  ArduinoOTA.begin();
  g_log.printf("[OTA] ready as '%s' @ %s\n",
               SANDY_OTA_HOSTNAME, WiFi.localIP().toString().c_str());
}

void setupTelnet() {
  g_telnetServer.begin();
  g_telnetServer.setNoDelay(true);
  g_log.printf("[TELNET] port 23 — connect via nc %s 23\n",
               WiFi.localIP().toString().c_str());
}

void updateTelnet() {
  if (g_telnetServer.hasClient()) {
    if (g_telnetClient && g_telnetClient.connected()) {
      WiFiClient n = g_telnetServer.available();
      n.println("[TELNET] busy"); n.stop();
    } else {
      g_telnetClient = g_telnetServer.available();
      g_telnetClient.println("=== ESP32-CAM serial mirror ===");
    }
  }
  if (g_telnetClient && !g_telnetClient.connected()) g_telnetClient.stop();
}

void startNetworkServicesIfReady() {
  if (g_networkServicesStarted) return;
  if (WiFi.status() != WL_CONNECTED) return;
  setupOTA();
  setupTelnet();
  setupMQTT();
  g_networkServicesStarted = true;
}
