// Speaker self-test: a repeating triple-beep on the MAX98357 amp.
// Uses I2S_NUM_1 TX only (the mics/ears use I2S_NUM_0, so no clash).

#include "config.h"
#if ENABLE_SPK_TEST

#include "sandy_spktest.h"
#include <math.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/i2s_std.h"

static const char *TAG = "spktest";

#define SR        16000          // sample rate
#define TONE_HZ   880            // beep pitch
#define AMP       4000           // reduced from 8000 for cleaner tone (safer, less distortion)
#define CHUNK     (SR / 50)      // 20 ms of samples = 320

static i2s_chan_handle_t s_tx;

static void play_tone(int ms) {
    static float phase = 0;
    float inc = 2.0f * (float)M_PI * TONE_HZ / SR;
    int16_t buf[CHUNK];
    int sent = 0, total = SR * ms / 1000;
    while (sent < total) {
        for (int i = 0; i < CHUNK; i++) {
            buf[i] = (int16_t)(AMP * sinf(phase));
            phase += inc;
            if (phase > 2.0f * (float)M_PI) phase -= 2.0f * (float)M_PI;
        }
        size_t w;
        i2s_channel_write(s_tx, buf, sizeof(buf), &w, portMAX_DELAY);
        sent += CHUNK;
    }
}

static void play_silence(int ms) {
    int16_t buf[CHUNK];
    memset(buf, 0, sizeof(buf));
    int sent = 0, total = SR * ms / 1000;
    while (sent < total) {
        size_t w;
        i2s_channel_write(s_tx, buf, sizeof(buf), &w, portMAX_DELAY);
        sent += CHUNK;
    }
}

static void spktest_task(void *arg) {
    i2s_chan_config_t cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&cfg, &s_tx, NULL));
    i2s_std_config_t std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SR),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_SPK_BCLK,
            .ws   = PIN_I2S_SPK_LRC,
            .dout = PIN_I2S_SPK_DIN,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = {0},
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_tx, &std));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx));

    for (;;) {
        ESP_LOGI(TAG, "beep x3");
        for (int i = 0; i < 3; i++) {
            play_tone(200);
            play_silence(150);
        }
        vTaskDelay(pdMS_TO_TICKS(2500));
    }
}

esp_err_t spktest_init(void) {
    xTaskCreate(spktest_task, "spktest", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "ready — speaker triple-beep test");
    return ESP_OK;
}

#endif // ENABLE_SPK_TEST
