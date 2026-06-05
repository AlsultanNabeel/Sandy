#include "sandy_mic.h"
#include "sandy_types.h"
#include "sandy_buzzer.h"
#include "sandy_face.h"
#include "config.h"
#include "esp_adc/adc_oneshot.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_timer.h"

static const char *TAG = "mic";
static adc_oneshot_unit_handle_t s_adc;

static void _task(void *arg) {
    int64_t last_clap_ms = 0;
    for (;;) {
        int raw = 0;
        adc_oneshot_read(s_adc, MIC_ADC_CHANNEL, &raw);

        int64_t now_ms = esp_timer_get_time() / 1000;
        if (raw > MIC_CLAP_THRESHOLD &&
            now_ms - last_clap_ms > MIC_CLAP_COOLDOWN_MS) {
            ESP_LOGI(TAG, "clap detected raw=%d", raw);
            g_current_mood = MOOD_SURPRISED;
            face_set_mood(MOOD_SURPRISED);
            buzzer_play(MELODY_HAPPY);
            last_clap_ms = now_ms;
        }
        vTaskDelay(pdMS_TO_TICKS(MIC_SAMPLE_PERIOD_MS));
    }
}

esp_err_t mic_init(void) {
    adc_oneshot_unit_init_cfg_t unit_cfg = {
        .unit_id  = ADC_UNIT_1,
        .ulp_mode = ADC_ULP_MODE_DISABLE,
    };
    ESP_ERROR_CHECK(adc_oneshot_new_unit(&unit_cfg, &s_adc));

    adc_oneshot_chan_cfg_t chan_cfg = {
        .bitwidth = ADC_BITWIDTH_12,
        .atten    = ADC_ATTEN_DB_12,
    };
    ESP_ERROR_CHECK(adc_oneshot_config_channel(s_adc, MIC_ADC_CHANNEL, &chan_cfg));

    xTaskCreate(_task, "mic", 2048, NULL, 4, NULL);
    ESP_LOGI(TAG, "ready on ADC1 CH%d (GPIO%d)", MIC_ADC_CHANNEL, PIN_MIC_ADC);
    return ESP_OK;
}
