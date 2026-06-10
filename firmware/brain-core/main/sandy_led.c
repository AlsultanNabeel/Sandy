// On-board WS2812 status LED (GPIO 48) — the "is she listening?" indicator.
// One pixel, RMT-driven. States are set from the voice link; writes are cheap
// enough to call from any task.

#include "config.h"
#if ENABLE_LED

#include "sandy_led.h"
#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "led";

static led_strip_handle_t s_strip;

esp_err_t led_init(void) {
    led_strip_config_t strip_cfg = {
        .strip_gpio_num = PIN_W2812,
        .max_leds = 1,
    };
    led_strip_rmt_config_t rmt_cfg = {
        .resolution_hz = 10 * 1000 * 1000,
    };
    esp_err_t err = led_strip_new_rmt_device(&strip_cfg, &rmt_cfg, &s_strip);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "init failed: %s", esp_err_to_name(err));
        return err;
    }
    led_set_state(LED_STATE_IDLE);
    ESP_LOGI(TAG, "ready on GPIO %d", PIN_W2812);
    return ESP_OK;
}

void led_set_state(sandy_led_state_t state) {
    if (!s_strip) return;
    switch (state) {
    case LED_STATE_IDLE:       // dim blue: awake, local wake word only
        led_strip_set_pixel(s_strip, 0, 0, 0, 12);
        break;
    case LED_STATE_LISTENING:  // white: session open, audio leaves the device
        led_strip_set_pixel(s_strip, 0, 40, 40, 40);
        break;
    case LED_STATE_TALKING:    // warm amber while Sandy speaks
        led_strip_set_pixel(s_strip, 0, 40, 18, 0);
        break;
    case LED_STATE_OFF:
    default:
        led_strip_set_pixel(s_strip, 0, 0, 0, 0);
        break;
    }
    led_strip_refresh(s_strip);
}

#endif // ENABLE_LED
