// =========================
// Sandy — MQTT (HiveMQ Cloud)
// =========================
// Topics:
//   sandy/cmd/mood        ← payload: "happy" / "sad" / إلخ
//   sandy/cmd/servo       ← payload: "90"
//   sandy/cmd/buzzer      ← payload: "alert"
//   sandy/cmd/base        ← payload: "forward"
//   sandy/cmd/autonomous  ← payload: "true" / "false"
//   sandy/status (publish) → JSON كل 5 ثواني
//   sandy/event  (publish) → أحداث (مسافة إلخ)

// forward decls عبر sandy_protos.h (مضمّن من sandy.ino)

static WiFiClientSecure g_mqttTcp;
static PubSubClient     g_mqtt(g_mqttTcp);
static unsigned long    g_lastMqttAttemptMs = 0;
static unsigned long    g_lastStatusPubMs   = 0;
static const unsigned long MQTT_RECONNECT_INTERVAL_MS = 5000;

static void mqttCallback(char* topic, byte* payload, unsigned int length) {
  // نسخ الـ payload لـ String آمن
  String value;
  value.reserve(length);
  for (unsigned int i = 0; i < length; i++) value += (char)payload[i];

  String t(topic);
  g_log.printf("[MQTT] %s = %s\n", t.c_str(), value.c_str());

  if (t == "sandy/cmd/mood") {
    moodState = value;
    onMoodStateChange();
  } else if (t == "sandy/cmd/servo") {
    servoAngle = value.toInt();
    onServoAngleChange();
  } else if (t == "sandy/cmd/buzzer") {
    buzzerCommand = value;
    onBuzzerCommandChange();
  } else if (t == "sandy/cmd/base") {
    baseAction = value;
    onBaseActionChange();
  } else if (t == "sandy/cmd/autonomous") {
    autonomousMode = (value == "true" || value == "1");
    onAutonomousModeChange();
  }
}

void setupMQTT() {
  g_mqttTcp.setInsecure();  // مؤقتاً — بدون verify cert. لاحقاً نضيف HiveMQ CA
  g_mqtt.setServer(SANDY_MQTT_HOST, SANDY_MQTT_PORT);
  g_mqtt.setCallback(mqttCallback);
  g_mqtt.setBufferSize(512);
  g_mqtt.setSocketTimeout(2);  // افتراضي ١٥s — يجمد اللوب لو HiveMQ بطيء
  g_log.printf("[MQTT] configured for %s:%d\n", SANDY_MQTT_HOST, SANDY_MQTT_PORT);
}

static bool mqttReconnect() {
  if (g_mqtt.connected()) return true;
  unsigned long now = millis();
  if (now - g_lastMqttAttemptMs < MQTT_RECONNECT_INTERVAL_MS) return false;
  g_lastMqttAttemptMs = now;

  String clientId = "sandy-esp32-";
  clientId += String((uint32_t)ESP.getEfuseMac(), HEX);

  g_log.printf("[MQTT] connecting as %s ...\n", clientId.c_str());
  if (g_mqtt.connect(clientId.c_str(), SANDY_MQTT_USER, SANDY_MQTT_PASS)) {
    g_log.println("[MQTT] connected");
    g_mqtt.subscribe("sandy/cmd/#", 1);
    return true;
  }
  g_log.printf("[MQTT] connect failed rc=%d\n", g_mqtt.state());
  return false;
}

static void publishStatus() {
  if (!g_mqtt.connected()) return;
  unsigned long now = millis();
  if (now - g_lastStatusPubMs < STATUS_POST_INTERVAL_MS) return;
  g_lastStatusPubMs = now;

  char buf[256];
  snprintf(buf, sizeof(buf),
           "{\"uptime_s\":%lu,\"rssi\":%d,\"heap\":%u,\"min_heap\":%u,"
           "\"wifi_drops\":%u,\"mood\":\"%s\",\"distance_cm\":%.1f,"
           "\"status_text\":\"%s\"}",
           now / 1000,
           WiFi.RSSI(),
           ESP.getFreeHeap(),
           ESP.getMinFreeHeap(),
           g_wifiDropCount,
           moodState.c_str(),
           distanceCm,
           statusText.c_str());
  g_mqtt.publish("sandy/status", buf, false);
}

void updateMQTT() {
  if (!g_networkServicesStarted) return;
  if (WiFi.status() != WL_CONNECTED) return;
  if (!g_mqtt.connected()) {
    mqttReconnect();
    return;
  }
  g_mqtt.loop();
  publishStatus();
}
