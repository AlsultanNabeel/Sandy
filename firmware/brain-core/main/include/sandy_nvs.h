#pragma once
#include <stdint.h>
#include "esp_err.h"

esp_err_t nvs_sandy_init(void);
esp_err_t nvs_load_servo_angle(uint8_t *out_angle);
esp_err_t nvs_save_servo_angle(uint8_t angle);
