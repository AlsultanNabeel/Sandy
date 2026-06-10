#pragma once
#include "esp_err.h"

// On-board WS2812 status LED — the privacy/state indicator:
//   idle      dim blue    "I'm here, listening only for the wake word"
//   listening white       "session open — audio is going to the cloud"
//   talking   warm amber  "Sandy is speaking"
typedef enum {
    LED_STATE_OFF = 0,
    LED_STATE_IDLE,
    LED_STATE_LISTENING,
    LED_STATE_TALKING,
} sandy_led_state_t;

esp_err_t led_init(void);
void      led_set_state(sandy_led_state_t state);
