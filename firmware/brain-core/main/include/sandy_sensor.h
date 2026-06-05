#pragma once
#include <stdint.h>
#include "esp_err.h"

esp_err_t sensor_init(void);
uint32_t  sensor_get_distance_cm(void);   // 0 = timeout / out of range
