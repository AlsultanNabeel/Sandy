#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_system.h"
#include "config.h"
#include "sandy_types.h"
#include "sandy_nvs.h"
#include "sandy_wifi.h"
#include "sandy_servo.h"
#include "sandy_buzzer.h"
#include "sandy_sensor.h"
#include "sandy_motors.h"
#include "sandy_touch.h"
#include "sandy_mic.h"
#include "sandy_face.h"
#include "sandy_mqtt.h"
#include "sandy_ota.h"
#include "sandy_voice.h"
#include "sandy_ears.h"
#include "sandy_spktest.h"
#include "sandy_remote.h"
#include "sandy_led.h"

static const char *TAG = "main";

// Global mood state — written by MQTT/touch/mic, read by face/buzzer
volatile sandy_mood_t g_current_mood = MOOD_IDLE;

#if ENABLE_SENSOR && ENABLE_FACE
// Permanent behaviour: when something comes close, Sandy looks surprised.
// Hands off while a voice conversation is running — the voice link drives the
// face then (listening/talking), and this loop would stomp it every 300ms.
static void _proximity_task(void *arg) {
    bool was_near = false;
    for (;;) {
#if ENABLE_VOICE
        if (voice_session_is_active()) {
            vTaskDelay(pdMS_TO_TICKS(300));
            continue;
        }
#endif
        uint32_t d = sensor_get_distance_cm();
        bool near = (d > 0 && d < 25);
        // Edge-triggered, not level-triggered: the old "set IDLE every 300ms"
        // stomped every mood that came from anywhere else (MQTT mood commands
        // appeared broken because of this) and would never let her fall asleep.
        if (near && !was_near) face_set_mood(MOOD_SURPRISED);
        if (!near && was_near) face_set_mood(MOOD_IDLE);
        was_near = near;
        vTaskDelay(pdMS_TO_TICKS(300));
    }
}
#endif

#if ENABLE_MQTT
// MQTT joins ~20s late on purpose: its TLS handshake right at boot stacked a
// power peak on top of the WiFi/display/voice bring-up and browned out weaker
// supplies (power bank / laptop USB). Body control can afford to be late.
static void _mqtt_late_start(void *arg) {
    vTaskDelay(pdMS_TO_TICKS(20000));
    if (mqtt_sandy_start() != ESP_OK) {
        ESP_LOGE(TAG, "MQTT failed to start — running without cloud body control");
    }
    vTaskDelete(NULL);
}
#endif

void app_main(void) {
    // reset_reason separates a brownout from a panic from a plain power-on at
    // a glance — the first thing to check when the board reboots on its own.
    ESP_LOGI(TAG, "Sandy Brain S3 — booting (reset_reason=%d)", (int)esp_reset_reason());

    // ── Core services ─────────────────────────────────────────────────────────
    ESP_ERROR_CHECK(nvs_sandy_init());
#if ENABLE_WIFI
    ESP_ERROR_CHECK(wifi_sandy_start());
#endif
#if ENABLE_REMOTE
    ESP_ERROR_CHECK(remote_init());   // OTA + remote log over WiFi
    // Repeat the reset reason now that the remote log buffer exists — the
    // line at the top of app_main is UART-only (printed before the buffer).
    ESP_LOGI(TAG, "reset_reason=%d (9=brownout 4=panic 1=power-on)", (int)esp_reset_reason());
#endif

    // ── Peripherals ───────────────────────────────────────────────────────────
#if ENABLE_FACE
    ESP_ERROR_CHECK(face_init());
#endif
#if ENABLE_LED
    led_init();   // non-fatal: a dead status LED shouldn't stop the robot
#endif
#if ENABLE_SERVO
    ESP_ERROR_CHECK(servo_init());
#endif
#if ENABLE_BUZZER
    ESP_ERROR_CHECK(buzzer_init());
#endif
#if ENABLE_SENSOR
    ESP_ERROR_CHECK(sensor_init());
#endif
#if ENABLE_MOTORS
    ESP_ERROR_CHECK(motors_init());
#endif
#if ENABLE_TOUCH
    ESP_ERROR_CHECK(touch_init());
#endif
#if ENABLE_MIC
    ESP_ERROR_CHECK(mic_init());
#endif
#if ENABLE_EARS
    ESP_ERROR_CHECK(ears_init());
#endif
#if ENABLE_SPK_TEST
    ESP_ERROR_CHECK(spktest_init());
#endif
#if ENABLE_OTA
    ESP_ERROR_CHECK(ota_init());
#endif

    // ── Network ───────────────────────────────────────────────────────────────
#if ENABLE_MQTT
    xTaskCreate(_mqtt_late_start, "mqtt_late", 4096, NULL, 3, NULL);
#endif

    // ── Voice link (waits for Wi-Fi, then connects to /voice) ───────────────────
#if ENABLE_VOICE
    ESP_ERROR_CHECK(voice_init());
#endif

    ESP_LOGI(TAG, "all systems go");

#if ENABLE_BUZZER
    // Short startup chime so we know the board booted.
    buzzer_play(MELODY_BOOT);
#endif

#if ENABLE_SENSOR && ENABLE_FACE
    xTaskCreate(_proximity_task, "proximity", 3072, NULL, 3, NULL);
#endif

    // Watchdog on main task (5s — configured in sdkconfig.defaults)
    esp_task_wdt_add(NULL);
    for (;;) {
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
