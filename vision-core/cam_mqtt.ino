// =========================
// ESP32-CAM — MQTT (HiveMQ)
// =========================

static WiFiClientSecure g_mqttTcp;
static PubSubClient     g_mqtt(g_mqttTcp);
static unsigned long    g_lastMqttAttemptMs = 0;

// Rate limit / safety: حد أدنى للفترة بين طلبات snapshot — حماية من الحرارة + spam
#define SNAPSHOT_MIN_GAP_MS  1500
static unsigned long g_lastSnapshotAtMs = 0;

static void mqttCallback(char* topic, byte* payload, unsigned int length) {
  String value;
  value.reserve(length);
  for (unsigned int i = 0; i < length; i++) value += (char)payload[i];
  g_log.printf("[MQTT] %s = %s\n", topic, value.c_str());

  g_log.printf("[CB] entered, topic_len=%u\n", (unsigned)strlen(topic));
  String t(topic);
  g_log.printf("[CB] topic str = '%s'\n", t.c_str());
  if (t == "sandy/cam/request") {
    g_log.println("[CB] matched cam/request");
    unsigned long now = millis();
    if (g_snapshotPending) {
      g_log.println("[CAM] snapshot already pending — ignoring duplicate request");
      return;
    }
    if (g_lastSnapshotAtMs && now - g_lastSnapshotAtMs < SNAPSHOT_MIN_GAP_MS) {
      g_log.printf("[CAM] rate-limited — last snap was %lums ago\n", now - g_lastSnapshotAtMs);
      return;
    }
    g_lastSnapshotAtMs = now;

    String id;
    int idx = value.indexOf("\"id\":\"");
    if (idx < 0) idx = value.indexOf("\"id\": \"");
    if (idx >= 0) {
      int start = value.indexOf("\"", idx + 4) + 1;
      int end = value.indexOf("\"", start);
      if (end > start) id = value.substring(start, end);
    }
    if (id.length() == 0) id = String(millis());
    g_currentRequestId = id;
    g_snapshotPending = true;
    g_log.printf("[CAM] snapshot requested id=%s\n", id.c_str());
  } else {
    g_log.println("[CB] topic did NOT match cam/request");
  }
}

void setupMQTT() {
  g_mqttTcp.setInsecure();
  g_mqtt.setServer(SANDY_MQTT_HOST, SANDY_MQTT_PORT);
  g_mqtt.setCallback(mqttCallback);
  g_mqtt.setBufferSize(MQTT_BUFFER_SIZE);  // كبير لاستيعاب chunks
  g_mqtt.setSocketTimeout(15);             // مهلة كافية لـ TLS handshake
  g_mqtt.setKeepAlive(30);                 // keepalive معقول
  g_log.printf("[MQTT] configured for %s:%d\n", SANDY_MQTT_HOST, SANDY_MQTT_PORT);
}

static bool mqttReconnect() {
  if (g_mqtt.connected()) return true;
  unsigned long now = millis();
  if (now - g_lastMqttAttemptMs < MQTT_RECONNECT_INTERVAL_MS) return false;
  g_lastMqttAttemptMs = now;

  String clientId = "sandy-cam-";
  clientId += String((uint32_t)ESP.getEfuseMac(), HEX);

  g_log.printf("[MQTT] connecting as %s ...\n", clientId.c_str());
  if (g_mqtt.connect(clientId.c_str(), SANDY_MQTT_USER, SANDY_MQTT_PASS)) {
    g_log.println("[MQTT] connected");
    g_mqtt.subscribe("sandy/cam/request", 0);  // QoS 0 — لا PUBACK يعلّق الـ TLS write
    return true;
  }
  g_log.printf("[MQTT] connect failed rc=%d\n", g_mqtt.state());
  return false;
}

static void publishCamStatus() {
  if (!g_mqtt.connected()) return;
  unsigned long now = millis();
  if (now - g_lastStatusPubMs < STATUS_POST_INTERVAL_MS) return;
  g_lastStatusPubMs = now;

  char buf[200];
  snprintf(buf, sizeof(buf),
           "{\"uptime_s\":%lu,\"rssi\":%d,\"heap\":%u,\"psram\":%u,"
           "\"camera_ready\":%s}",
           now / 1000,
           WiFi.RSSI(),
           ESP.getFreeHeap(),
           ESP.getFreePsram(),
           g_cameraReady ? "true" : "false");
  g_mqtt.publish("sandy/cam/status", buf, false);

  // heartbeat واضح ع التيلنت — يأكد إنو الـ loop شغّال
  g_log.printf("[HB] up=%lus rssi=%d heap=%u cam=%s mqtt=ok\n",
               now / 1000, WiFi.RSSI(), ESP.getFreeHeap(),
               g_cameraReady ? "yes" : "NO");
}

void updateMQTT() {
  static unsigned long lastNoWifiLogMs = 0;
  if (WiFi.status() != WL_CONNECTED) {
    unsigned long now = millis();
    if (now - lastNoWifiLogMs > 5000) {
      lastNoWifiLogMs = now;
      g_log.println("[HB] waiting for WiFi...");
    }
    return;
  }
  if (!g_mqtt.connected()) { mqttReconnect(); return; }
  g_mqtt.loop();
  publishCamStatus();
}

// تُستخدم من cam_capture.ino لنشر chunk
bool mqttPublishChunk(const char* payload, unsigned int len) {
  if (!g_mqtt.connected()) return false;
  return g_mqtt.publish("sandy/cam/snapshot", (const uint8_t*)payload, len, false);
}

void mqttPublishEvent(const char* json) {
  if (!g_mqtt.connected()) return;
  g_mqtt.publish("sandy/cam/event", json, false);
}
