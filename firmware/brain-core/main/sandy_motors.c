#include "sandy_motors.h"
#include "config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/timers.h"

static const char *TAG = "motors";
static TimerHandle_t s_wdt;

#define _SET(p, v) gpio_set_level((p), (v))

static void _stop_raw(void) {
    _SET(PIN_MOTOR_IN1, 0); _SET(PIN_MOTOR_IN2, 0);
    _SET(PIN_MOTOR_IN3, 0); _SET(PIN_MOTOR_IN4, 0);
}

static void _wdt_cb(TimerHandle_t t) {
    _stop_raw();
    ESP_LOGW(TAG, "watchdog: auto-stopped");
}

esp_err_t motors_init(void) {
    uint64_t mask = (1ULL << PIN_MOTOR_IN1) | (1ULL << PIN_MOTOR_IN2) |
                    (1ULL << PIN_MOTOR_IN3) | (1ULL << PIN_MOTOR_IN4);
    gpio_config_t cfg = { .pin_bit_mask = mask, .mode = GPIO_MODE_OUTPUT };
    gpio_config(&cfg);
    _stop_raw();

    s_wdt = xTimerCreate("mwdt", pdMS_TO_TICKS(MOTOR_WATCHDOG_MS),
                          pdFALSE, NULL, _wdt_cb);
    ESP_LOGI(TAG, "ready");
    return ESP_OK;
}

void motors_command(motor_cmd_t cmd, uint32_t duration_ms) {
    _stop_raw();
    switch (cmd) {
        case MOTOR_FORWARD:
            _SET(PIN_MOTOR_IN1,1);_SET(PIN_MOTOR_IN2,0);
            _SET(PIN_MOTOR_IN3,1);_SET(PIN_MOTOR_IN4,0); break;
        case MOTOR_BACKWARD:
            _SET(PIN_MOTOR_IN1,0);_SET(PIN_MOTOR_IN2,1);
            _SET(PIN_MOTOR_IN3,0);_SET(PIN_MOTOR_IN4,1); break;
        case MOTOR_LEFT:
            _SET(PIN_MOTOR_IN1,0);_SET(PIN_MOTOR_IN2,1);
            _SET(PIN_MOTOR_IN3,1);_SET(PIN_MOTOR_IN4,0); break;
        case MOTOR_RIGHT:
            _SET(PIN_MOTOR_IN1,1);_SET(PIN_MOTOR_IN2,0);
            _SET(PIN_MOTOR_IN3,0);_SET(PIN_MOTOR_IN4,1); break;
        default: return;
    }
    ESP_LOGI(TAG, "cmd=%d dur=%lums", (int)cmd, (unsigned long)duration_ms);
    xTimerReset(s_wdt, 0);
    if (duration_ms > 0) {
        vTaskDelay(pdMS_TO_TICKS(duration_ms));
        motors_stop();
    }
}

void motors_stop(void) {
    _stop_raw();
    xTimerStop(s_wdt, 0);
}
