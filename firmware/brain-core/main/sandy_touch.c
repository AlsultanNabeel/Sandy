#include "sandy_touch.h"
#include "sandy_types.h"
#include "sandy_buzzer.h"
#include "sandy_face.h"
#include "config.h"
#include "driver/gpio.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "touch";

static void _task(void *arg) {
    bool prev = false;
    for (;;) {
        bool cur = gpio_get_level(PIN_TOUCH);
        if (cur && !prev) {
            ESP_LOGI(TAG, "head pat");
            g_current_mood = MOOD_BIG_HAPPY;
            face_set_mood(MOOD_BIG_HAPPY);
            buzzer_play(MELODY_HAPPY);
        }
        prev = cur;
        vTaskDelay(pdMS_TO_TICKS(TOUCH_DEBOUNCE_MS));
    }
}

esp_err_t touch_init(void) {
    gpio_config_t cfg = {
        .pin_bit_mask   = 1ULL << PIN_TOUCH,
        .mode           = GPIO_MODE_INPUT,
        .pull_down_en   = GPIO_PULLDOWN_ENABLE,
    };
    gpio_config(&cfg);
    xTaskCreate(_task, "touch", 2048, NULL, 4, NULL);
    ESP_LOGI(TAG, "ready on GPIO%d", PIN_TOUCH);
    return ESP_OK;
}
