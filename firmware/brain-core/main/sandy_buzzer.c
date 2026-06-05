#include "sandy_buzzer.h"
#include "config.h"
#include "driver/ledc.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

static const char *TAG = "buzzer";

typedef struct { uint32_t freq; uint32_t ms; } note_t;

// Melody definitions (freq=0 → rest)
static const note_t BOOT[]    = {{523,100},{659,100},{784,150},{0,50},{1047,200}};
static const note_t HAPPY[]   = {{784,100},{880,100},{1047,200}};
static const note_t CURIOUS[] = {{523,80},{587,80},{659,80},{698,120}};
static const note_t SAD[]     = {{494,200},{440,200},{392,300}};
static const note_t ALERT[]   = {{880,100},{0,50},{880,100},{0,50},{880,150}};
static const note_t ERR[]     = {{330,200},{0,50},{294,200},{0,50},{262,300}};

typedef struct { const note_t *notes; size_t count; } melody_def_t;
static const melody_def_t s_defs[MELODY_COUNT] = {
    [MELODY_NONE]    = {NULL,  0},
    [MELODY_BOOT]    = {BOOT,    5},
    [MELODY_HAPPY]   = {HAPPY,   3},
    [MELODY_CURIOUS] = {CURIOUS, 4},
    [MELODY_SAD]     = {SAD,     3},
    [MELODY_ALERT]   = {ALERT,   5},
    [MELODY_ERROR]   = {ERR,     5},
};

static QueueHandle_t s_q;

static void _note(uint32_t freq, uint32_t ms) {
    if (freq == 0) {
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER, 0);
    } else {
        ledc_set_freq(LEDC_LOW_SPEED_MODE, LEDC_TIMER_BUZZER, freq);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER, BUZZER_VOLUME);
    }
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER);
    vTaskDelay(pdMS_TO_TICKS(ms));
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER, 0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER);
}

static void _task(void *arg) {
    sandy_melody_t m;
    for (;;) {
        if (xQueueReceive(s_q, &m, portMAX_DELAY) != pdTRUE) continue;
        const melody_def_t *def = &s_defs[m < MELODY_COUNT ? m : MELODY_NONE];
        for (size_t i = 0; i < def->count; i++) {
            sandy_melody_t next;
            if (xQueuePeek(s_q, &next, 0) == pdTRUE) {
                // Preempt: switch to newer melody
                xQueueReceive(s_q, &next, 0);
                m = next;
                def = &s_defs[m < MELODY_COUNT ? m : MELODY_NONE];
                i = (size_t)-1;
                continue;
            }
            _note(def->notes[i].freq, def->notes[i].ms);
        }
    }
}

esp_err_t buzzer_init(void) {
    ledc_timer_config_t timer = {
        .speed_mode      = LEDC_LOW_SPEED_MODE,
        .duty_resolution = BUZZER_RESOLUTION,
        .timer_num       = LEDC_TIMER_BUZZER,
        .freq_hz         = 2000,
        .clk_cfg         = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));

    ledc_channel_config_t ch = {
        .gpio_num   = PIN_BUZZER,
        .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel    = LEDC_CH_BUZZER,
        .timer_sel  = LEDC_TIMER_BUZZER,
        .duty       = 0,
        .hpoint     = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&ch));

    s_q = xQueueCreate(3, sizeof(sandy_melody_t));
    xTaskCreate(_task, "buzzer", 2048, NULL, 5, NULL);
    ESP_LOGI(TAG, "ready");
    return ESP_OK;
}

void buzzer_play(sandy_melody_t melody) {
    xQueueSend(s_q, &melody, 0);
}

void buzzer_stop(void) {
    xQueueReset(s_q);
    ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER, 0);
    ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CH_BUZZER);
}
