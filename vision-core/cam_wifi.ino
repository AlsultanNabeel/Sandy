// =========================
// ESP32-CAM — WiFi + Diagnostics
// =========================

static unsigned long g_lastWifiAttemptMs = 0;
static uint32_t      g_wifiDropCount     = 0;

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      Serial.printf("[WIFI] connected ssid='%s'\n",
                    (char*)info.wifi_sta_connected.ssid);
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      Serial.printf("[WIFI] got IP=%s rssi=%d\n",
                    WiFi.localIP().toString().c_str(), WiFi.RSSI());
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
      g_wifiDropCount++;
      Serial.printf("[WIFI] disconnected reason=%d drops=%u\n",
                    info.wifi_sta_disconnected.reason, g_wifiDropCount);
      break;
    default: break;
  }
}

void connectWiFi() {
  Serial.printf("[WIFI] connecting to '%s' ...\n", SECRET_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(SECRET_SSID, SECRET_OPTIONAL_PASS);
}

void ensureWiFiConnected() {
  if (WiFi.status() == WL_CONNECTED) return;
  unsigned long now = millis();
  if (now - g_lastWifiAttemptMs < WIFI_RECONNECT_INTERVAL_MS) return;
  g_lastWifiAttemptMs = now;
  WiFi.disconnect();
  WiFi.begin(SECRET_SSID, SECRET_OPTIONAL_PASS);
}
