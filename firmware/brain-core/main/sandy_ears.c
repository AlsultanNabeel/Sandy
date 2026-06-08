// Sound-direction sensing from a stereo INMP441 pair.
//
// Both mics sit on one I2S bus (shared SCK/WS/SD); the left mic has L/R→GND
// (left slot) and the right mic L/R→VDD (right slot). We read both channels,
// measure how loud each is over a short window, and glance the eyes toward the
// louder side. Fully local — no cloud needed.

#include "sandy_ears.h"
#include "config.h"
#include "sandy_face.h"

#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "driver/i2s_std.h"

static const char *TAG = "ears";

static i2s_chan_handle_t s_rx;

#define FRAMES        512    // stereo frames per read (~32 ms at 16 kHz)
#define EARS_SHIFT    12     // bring the 24-bit INMP441 sample into a sane range
#define EARS_THRESH   2000   // mean level below this (per ch) = ambient, ignore
#define EARS_BIAS     8      // |pan| under this = treat as centre (mic mismatch)

static int32_t s_buf[FRAMES * 2];

static esp_err_t i2s_start(void) {
    i2s_chan_config_t cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&cfg, NULL, &s_rx));
    i2s_std_config_t std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(VOICE_IN_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_MIC_SCK,
            .ws   = PIN_I2S_MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = PIN_I2S_MIC_SD,
            .invert_flags = {0},
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx, &std));
    ESP_ERROR_CHECK(i2s_channel_enable(s_rx));
    return ESP_OK;
}

static void ears_task(void *arg) {
    int64_t last_log = 0;
    for (;;) {
        size_t br = 0;
        if (i2s_channel_read(s_rx, s_buf, sizeof(s_buf), &br, portMAX_DELAY) != ESP_OK)
            continue;
        int n = br / (2 * sizeof(int32_t));
        if (n <= 0) continue;

        int64_t sumL = 0, sumR = 0;
        for (int i = 0; i < n; i++) {
            int32_t l = s_buf[2 * i]     >> EARS_SHIFT;
            int32_t r = s_buf[2 * i + 1] >> EARS_SHIFT;
            sumL += (l < 0) ? -l : l;
            sumR += (r < 0) ? -r : r;
        }
        int levelL = (int)(sumL / n);
        int levelR = (int)(sumR / n);
        int total  = levelL + levelR;

        if (total > 2 * EARS_THRESH) {
            int pan = (levelR - levelL) * 100 / (total ? total : 1);  // - = left, + = right
            if (pan > -EARS_BIAS && pan < EARS_BIAS) pan = 0;         // dead-zone = centre
            face_look(pan);
        }

        int64_t now = esp_timer_get_time() / 1000;
        if (now - last_log > 400) {     // don't flood the log
            last_log = now;
            ESP_LOGI(TAG, "L=%d R=%d", levelL, levelR);
        }
    }
}

esp_err_t ears_init(void) {
    ESP_ERROR_CHECK(i2s_start());
    xTaskCreate(ears_task, "ears", 4096, NULL, 4, NULL);
    ESP_LOGI(TAG, "ready — stereo INMP441 direction sensing");
    return ESP_OK;
}
