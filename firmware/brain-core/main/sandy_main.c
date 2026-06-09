#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
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

static const char *TAG = "main";

// Global mood state — written by MQTT/touch/mic, read by face/buzzer
volatile sandy_mood_t g_current_mood = MOOD_IDLE;

#if ENABLE_SENSOR && ENABLE_FACE
// Permanent behaviour: when something comes close, Sandy looks surprised.
static void _proximity_task(void *arg) {
    for (;;) {
        uint32_t d = sensor_get_distance_cm();
        face_set_mood((d > 0 && d < 25) ? MOOD_SURPRISED : MOOD_IDLE);
        vTaskDelay(pdMS_TO_TICKS(300));
    }
}
#endif

void app_main(void) {
    ESP_LOGI(TAG, "Sandy Brain S3 — booting");

    // ── Core services ─────────────────────────────────────────────────────────
    ESP_ERROR_CHECK(nvs_sandy_init());
#if ENABLE_WIFI
    ESP_ERROR_CHECK(wifi_sandy_start());
#endif

    // ── Peripherals ───────────────────────────────────────────────────────────
#if ENABLE_FACE
    ESP_ERROR_CHECK(face_init());
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
    ESP_ERROR_CHECK(mqtt_sandy_start());
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
