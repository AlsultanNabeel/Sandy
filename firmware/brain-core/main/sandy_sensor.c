#include "sandy_sensor.h"
#include "config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdlib.h>

static const char *TAG = "sensor";
static volatile uint32_t s_dist_cm = 0;

static int _cmp(const void *a, const void *b) {
    uint32_t x = *(uint32_t *)a, y = *(uint32_t *)b;
    return (x > y) - (x < y);
}

static uint32_t _measure_once(void) {
    gpio_set_level(PIN_SENSOR_TRIG, 0);
    esp_rom_delay_us(2);
    gpio_set_level(PIN_SENSOR_TRIG, 1);
    esp_rom_delay_us(10);
    gpio_set_level(PIN_SENSOR_TRIG, 0);

    // Wait for echo HIGH
    int64_t t0 = esp_timer_get_time();
    while (!gpio_get_level(PIN_SENSOR_ECHO)) {
        if (esp_timer_get_time() - t0 > SENSOR_TIMEOUT_US) return 0;
    }

    // Measure echo HIGH duration
    int64_t t1 = esp_timer_get_time();
    while (gpio_get_level(PIN_SENSOR_ECHO)) {
        if (esp_timer_get_time() - t1 > SENSOR_TIMEOUT_US) return 0;
    }
    return (uint32_t)((esp_timer_get_time() - t1) / 58);  // us → cm
}

static void _task(void *arg) {
    uint32_t samples[SENSOR_MEDIAN_N];
    for (;;) {
        for (int i = 0; i < SENSOR_MEDIAN_N; i++) {
            samples[i] = _measure_once();
            vTaskDelay(pdMS_TO_TICKS(20));
        }
        qsort(samples, SENSOR_MEDIAN_N, sizeof(uint32_t), _cmp);
        s_dist_cm = samples[SENSOR_MEDIAN_N / 2];
        vTaskDelay(pdMS_TO_TICKS(SENSOR_POLL_MS - SENSOR_MEDIAN_N * 20));
    }
}

esp_err_t sensor_init(void) {
    gpio_config_t trig_cfg = {
        .pin_bit_mask = 1ULL << PIN_SENSOR_TRIG,
        .mode         = GPIO_MODE_OUTPUT,
    };
    gpio_config(&trig_cfg);
    gpio_set_level(PIN_SENSOR_TRIG, 0);

    gpio_config_t echo_cfg = {
        .pin_bit_mask = 1ULL << PIN_SENSOR_ECHO,
        .mode         = GPIO_MODE_INPUT,
    };
    gpio_config(&echo_cfg);

    xTaskCreate(_task, "sensor", 2048, NULL, 4, NULL);
    ESP_LOGI(TAG, "ready on trig=%d echo=%d", PIN_SENSOR_TRIG, PIN_SENSOR_ECHO);
    return ESP_OK;
}

uint32_t sensor_get_distance_cm(void) { return s_dist_cm; }
