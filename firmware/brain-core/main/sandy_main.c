#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
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

static const char *TAG = "main";

// Global mood state — written by MQTT/touch/mic, read by face/buzzer
volatile sandy_mood_t g_current_mood = MOOD_IDLE;

void app_main(void) {
    ESP_LOGI(TAG, "Sandy Brain S3 — booting");

    // ── Core services ─────────────────────────────────────────────────────────
    ESP_ERROR_CHECK(nvs_sandy_init());
    ESP_ERROR_CHECK(wifi_sandy_start());

    // ── Peripherals ───────────────────────────────────────────────────────────
    ESP_ERROR_CHECK(face_init());
    ESP_ERROR_CHECK(servo_init());
    ESP_ERROR_CHECK(buzzer_init());
    ESP_ERROR_CHECK(sensor_init());
    ESP_ERROR_CHECK(motors_init());
    ESP_ERROR_CHECK(touch_init());
    ESP_ERROR_CHECK(mic_init());
    ESP_ERROR_CHECK(ota_init());

    // ── Network ───────────────────────────────────────────────────────────────
    ESP_ERROR_CHECK(mqtt_sandy_start());

    ESP_LOGI(TAG, "all systems go");

    // Watchdog on main task (5s — configured in sdkconfig.defaults)
    esp_task_wdt_add(NULL);
    for (;;) {
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
