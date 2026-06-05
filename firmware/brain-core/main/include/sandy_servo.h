#pragma once
#include <stdint.h>
#include "esp_err.h"

esp_err_t servo_init(void);
void      servo_set_angle(uint8_t angle);   // clamped to safe range, sine-eased
uint8_t   servo_get_angle(void);
