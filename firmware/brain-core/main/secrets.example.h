#pragma once
// Copy this file to secrets.h and fill in your values.
// secrets.h is gitignored — never commit it.

#define WIFI_SSID           "YOUR_WIFI_SSID"
#define WIFI_PASS           "YOUR_WIFI_PASSWORD"

// HiveMQ Cloud — format: mqtts://xxxx.s1.eu.hivemq.cloud:8883
#define MQTT_BROKER_URI     "mqtts://YOUR_BROKER.hivemq.cloud:8883"
#define MQTT_CLIENT_ID      "sandy-brain-s3"
#define MQTT_USER           "YOUR_MQTT_USER"
#define MQTT_PASS           "YOUR_MQTT_PASS"

// Voice link to the cloud (/voice). The HMAC key must match the server's
// SANDY_WS_HMAC_KEY config var.
#define SANDY_VOICE_WS_URI  "wss://YOUR_APP.herokuapp.com/voice"
#define SANDY_WS_HMAC_KEY   "YOUR_WS_HMAC_KEY"
#define SANDY_DEVICE_ID     "sandy-brain-s3"
