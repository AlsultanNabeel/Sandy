#include "sandy_servo.h"
#include "sandy_nvs.h"
#include "config.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <math.h>
#include <stdlib.h>

static const char *TAG = "servo";
static uint8_t s_angle = SERVO_DEFAULT_POS;

// Map angle [0-180] to LEDC duty for 14-bit timer at 50Hz (period = 20000us)
static uint32_t _angle_to_duty(uint8_t angle) {
    uint32_t pw_us = SERVO_MIN_US +
        (uint32_t)angle * (SERVO_MAX_US - SERVO_MIN_US) / 180;
    return (pw_us * ((1 << 14) - 1)) / 20000;
}

esp_err_t servo_init(void) {
    ledc_timer_config_t timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = SERVO_RESOLUTION,
        .timer_num       = LEDC_TIMER_SERVO,
        .freq_hz         = SERVO_FREQ_HZ,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));

    ledc_channel_config_t ch = {
        .gpio_num   = PIN_SERVO,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CH_SERVO,
        .timer_sel  = LEDC_TIMER_SERVO,
        .duty       = _angle_to_duty(SERVO_DEFAULT_POS),
        .hpoint     = 0,
        .flags      = { .output_invert = 0 },
    };
    ESP_ERROR_CHECK(ledc_channel_config(&ch));

    uint8_t saved = SERVO_DEFAULT_POS;
    if (nvs_load_servo_angle(&saved) == ESP_OK) {
        ESP_LOGI(TAG, "restored angle=%d from NVS", saved);
    }
    servo_set_angle(saved);
    return ESP_OK;
}

void servo_set_angle(uint8_t angle) {
    if (angle < SERVO_SAFE_MIN) angle = SERVO_SAFE_MIN;
    if (angle > SERVO_SAFE_MAX) angle = SERVO_SAFE_MAX;
    if (angle == s_angle) return;

    // Sine ease in-out: smooth motion between s_angle → angle
    int from = s_angle, to = angle;
    int steps = abs(to - from);
    for (int i = 1; i <= steps; i++) {
        float t     = (float)i / (float)steps;
        float eased = (1.0f - cosf(t * (float)M_PI)) * 0.5f;
        uint8_t pos = (uint8_t)(from + (to - from) * eased);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_SERVO, _angle_to_duty(pos));
        ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_SERVO);
        vTaskDelay(pdMS_TO_TICKS(20));   // 50Hz
    }

    s_angle = angle;
    nvs_save_servo_angle(angle);
    ESP_LOGI(TAG, "angle=%d", angle);
}

uint8_t servo_get_angle(void) { return s_angle; }
