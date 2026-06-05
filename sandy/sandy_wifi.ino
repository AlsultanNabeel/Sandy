// =========================
// Sandy — WiFi + Diagnostics
// =========================
// connectWiFi / ensureWiFiConnected / WiFi event handler / DIAG logging

static unsigned long g_lastWifiAttemptMs = 0;
static unsigned long g_diagLastPrintMs   = 0;
static unsigned long g_lastWifiUpMs      = 0;
static unsigned long g_lastWifiDownMs    = 0;
// g_wifiDropCount مُعرَّف في sandy.ino (يستخدمه sandy_mqtt.ino كمان)

static const char* resetReasonStr(esp_reset_reason_t r) {
  switch (r) {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT_RESET";
    case ESP_RST_SW:        return "SW_RESET";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT⚡ (POWER!)";
    case ESP_RST_SDIO:      return "SDIO";
    default:                return "UNKNOWN";
  }
}

void logResetReason() {
  Serial.print("[DIAG] reset_reason = ");
  Serial.println(resetReasonStr(esp_reset_reason()));
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  unsigned long now = millis();
  switch (event) {
    case ARDUINO_EVENT_WIFI_STA_CONNECTED:
      g_log.printf("[WIFI] STA_CONNECTED ssid='%s'\n",
                   (char*)info.wifi_sta_connected.ssid);
      break;
    case ARDUINO_EVENT_WIFI_STA_GOT_IP:
      g_lastWifiUpMs = now;
      g_log.printf("[WIFI] GOT_IP ip=%s rssi=%d uptime=%lus\n",
                   WiFi.localIP().toString().c_str(),
                   WiFi.RSSI(), now / 1000);
      break;
    case ARDUINO_EVENT_WIFI_STA_DISCONNECTED: {
      g_wifiDropCount++;
      g_lastWifiDownMs = now;
      unsigned long upDuration = (g_lastWifiUpMs > 0) ? (now - g_lastWifiUpMs) : 0;
      g_log.printf("[WIFI] DISCONNECTED reason=%d drops=%u was_up_for=%lums\n",
                   info.wifi_sta_disconnected.reason,
                   g_wifiDropCount, upDuration);
      break;
    }
    default: break;
  }
}

void logDiagnostics() {
  unsigned long now = millis();
  static uint32_t lastDropCount = 0;

  // اطبع فقط في حالتين: (1) حصل drop جديد، (2) heartbeat كل 60 ثانية
  bool dropOccurred = (g_wifiDropCount != lastDropCount);
  bool heartbeatDue = (now - g_diagLastPrintMs >= 60000UL);
  if (!dropOccurred && !heartbeatDue) return;

  g_diagLastPrintMs = now;
  lastDropCount     = g_wifiDropCount;

  g_log.printf("[DIAG] up=%lus wifi=%s rssi=%d heap=%u min_heap=%u wifi_drops=%u%s\n",
               now / 1000,
               WiFi.status() == WL_CONNECTED ? "UP" : "DOWN",
               WiFi.RSSI(),
               ESP.getFreeHeap(),
               ESP.getMinFreeHeap(),
               g_wifiDropCount,
               dropOccurred ? "  ← DROP" : "");
}

void connectWiFi() {
  Serial.printf("[WIFI] connecting to '%s' ...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // مهم — يمنع انقطاعات على إشارة ضعيفة
  WiFi.setAutoReconnect(true);   // ESP32 يعيد المحاولة تلقائياً
  WiFi.persistent(false);        // لا تكتب credentials للـ NVS كل مرة
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}

void ensureWiFiConnected() {
  if (WiFi.status() == WL_CONNECTED) return;
  unsigned long now = millis();
  if (now - g_lastWifiAttemptMs < WIFI_RECONNECT_INTERVAL_MS) return;
  g_lastWifiAttemptMs = now;
  Serial.println("[WIFI] manual reconnect attempt");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
}
