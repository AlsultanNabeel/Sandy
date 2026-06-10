#include "sandy_mqtt.h"
#include "sandy_types.h"
#include "sandy_servo.h"
#include "sandy_buzzer.h"
#include "sandy_motors.h"
#include "sandy_sensor.h"
#include "sandy_face.h"
#include "sandy_ota.h"
#include "config.h"
#include "secrets.h"
#include "mqtt_client.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

static const char *TAG = "mqtt";
static esp_mqtt_client_handle_t s_client = NULL;

// ─── Topic handlers ───────────────────────────────────────────────────────────

static const struct { const char *name; sandy_mood_t mood; } MOOD_MAP[] = {
    {"idle",        MOOD_IDLE},       {"happy",       MOOD_HAPPY},
    {"curious",     MOOD_CURIOUS},    {"sad",         MOOD_SAD},
    {"alert",       MOOD_ALERT},      {"surprised",   MOOD_SURPRISED},
    {"big_happy",   MOOD_BIG_HAPPY},  {"focused",     MOOD_FOCUSED},
    {"bored",       MOOD_BORED},      {"excited",     MOOD_EXCITED},
    {"love",        MOOD_LOVE},       {"angry",       MOOD_ANGRY},
    {"confused",    MOOD_CONFUSED},   {"thinking",    MOOD_THINKING},
    {"sleepy",      MOOD_SLEEPY},     {"shy",         MOOD_SHY},
    {"proud",       MOOD_PROUD},      {"worried",     MOOD_WORRIED},
    {"playful",     MOOD_PLAYFUL},    {"calm",        MOOD_CALM},
    {"grumpy",      MOOD_GRUMPY},     {"hopeful",     MOOD_HOPEFUL},
    {"grateful",    MOOD_GRATEFUL},   {"disappointed",MOOD_DISAPPOINTED},
    {"silly",       MOOD_SILLY},
};

static void _handle_mood(const char *val) {
    for (size_t i = 0; i < sizeof(MOOD_MAP)/sizeof(MOOD_MAP[0]); i++) {
        if (!strcmp(val, MOOD_MAP[i].name)) {
            g_current_mood = MOOD_MAP[i].mood;
            face_set_mood(MOOD_MAP[i].mood);
            return;
        }
    }
    ESP_LOGW(TAG, "unknown mood: %s", val);
}

static void _handle_servo(const char *val) {
    int angle = atoi(val);
    if (angle >= 0 && angle <= 180) servo_set_angle((uint8_t)angle);
}

static void _handle_buzzer(const char *val) {
    if      (!strcmp(val, "boot"))    buzzer_play(MELODY_BOOT);
    else if (!strcmp(val, "happy"))   buzzer_play(MELODY_HAPPY);
    else if (!strcmp(val, "curious")) buzzer_play(MELODY_CURIOUS);
    else if (!strcmp(val, "sad"))     buzzer_play(MELODY_SAD);
    else if (!strcmp(val, "alert"))   buzzer_play(MELODY_ALERT);
    else if (!strcmp(val, "error"))   buzzer_play(MELODY_ERROR);
    else ESP_LOGW(TAG, "unknown melody: %s", val);
}

static void _handle_base(const char *val) {
    if      (!strcmp(val, "forward"))  motors_command(MOTOR_FORWARD,  0);
    else if (!strcmp(val, "backward")) motors_command(MOTOR_BACKWARD, 0);
    else if (!strcmp(val, "left"))     motors_command(MOTOR_LEFT,     0);
    else if (!strcmp(val, "right"))    motors_command(MOTOR_RIGHT,    0);
    else if (!strcmp(val, "stop"))     motors_stop();
    else ESP_LOGW(TAG, "unknown base cmd: %s", val);
}

// ─── MQTT event handler ───────────────────────────────────────────────────────

static void _handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    esp_mqtt_event_handle_t ev = (esp_mqtt_event_handle_t)data;
    switch ((esp_mqtt_event_id_t)id) {
        case MQTT_EVENT_CONNECTED:
            ESP_LOGI(TAG, "connected");
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/mood",       1);
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/servo",      1);
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/buzzer",     1);
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/base",       1);
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/autonomous", 1);
            esp_mqtt_client_subscribe(s_client, "sandy/cmd/ota",        1);
            buzzer_play(MELODY_BOOT);
            break;

        case MQTT_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "disconnected — will auto-reconnect");
            break;

        case MQTT_EVENT_DATA: {
            if (!ev->topic || !ev->data) break;
            char topic[64]  = {0};
            char val[256]   = {0};
            int  tlen = ev->topic_len  < 63  ? ev->topic_len  : 63;
            int  dlen = ev->data_len   < 255 ? ev->data_len   : 255;
            memcpy(topic, ev->topic, tlen);
            memcpy(val,   ev->data,  dlen);
            ESP_LOGD(TAG, "%s = %s", topic, val);

            if      (!strcmp(topic, "sandy/cmd/mood"))      _handle_mood(val);
            else if (!strcmp(topic, "sandy/cmd/servo"))     _handle_servo(val);
            else if (!strcmp(topic, "sandy/cmd/buzzer"))    _handle_buzzer(val);
            else if (!strcmp(topic, "sandy/cmd/base"))      _handle_base(val);
            else if (!strcmp(topic, "sandy/cmd/autonomous"))
                ESP_LOGI(TAG, "autonomous=%s (TODO)", val);
            else if (!strcmp(topic, "sandy/cmd/ota"))
                ota_trigger(val);
            break;
        }

        case MQTT_EVENT_ERROR:
            ESP_LOGE(TAG, "error type=%d",
                     ev->error_handle ? ev->error_handle->error_type : -1);
            break;

        default: break;
    }
}

// ─── Status publisher ─────────────────────────────────────────────────────────

void mqtt_publish_status(void) {
    if (!s_client) return;
    char buf[256];
    snprintf(buf, sizeof(buf),
        "{\"uptime\":%lld,\"heap\":%lu,\"mood\":%d,\"distance\":%lu}",
        esp_timer_get_time() / 1000000LL,
        (unsigned long)esp_get_free_heap_size(),
        (int)g_current_mood,
        (unsigned long)sensor_get_distance_cm());
    esp_mqtt_client_publish(s_client, "sandy/status", buf, 0, 0, 0);
}

static void _status_task(void *arg) {
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(MQTT_STATUS_INTERVAL_MS));
        mqtt_publish_status();
    }
}

// ─── Init ─────────────────────────────────────────────────────────────────────

esp_err_t mqtt_sandy_start(void) {
    esp_mqtt_client_config_t cfg = {
        .broker = {
            .address = { .uri = MQTT_BROKER_URI },
            // Real TLS via the built-in CA bundle (same as the voice WSS link).
            // skip_cert_common_name_check alone doesn't work here: esp-tls
            // refuses to connect with no verification source at all.
            .verification = { .crt_bundle_attach = esp_crt_bundle_attach },
        },
        .credentials = {
            .client_id  = MQTT_CLIENT_ID,
            .username   = MQTT_USER,
            .authentication = { .password = MQTT_PASS },
        },
        .network = { .reconnect_timeout_ms = MQTT_RECONNECT_MS },
    };

    s_client = esp_mqtt_client_init(&cfg);
    if (!s_client) return ESP_FAIL;

    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, _handler, NULL);
    esp_mqtt_client_start(s_client);

    xTaskCreate(_status_task, "mqtt_status", 3072, NULL, 4, NULL);
    ESP_LOGI(TAG, "started → %s", MQTT_BROKER_URI);
    return ESP_OK;
}
