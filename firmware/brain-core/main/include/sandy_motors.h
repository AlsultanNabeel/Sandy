#pragma once
#include "sandy_types.h"
#include "esp_err.h"

esp_err_t motors_init(void);
void      motors_command(motor_cmd_t cmd, uint32_t duration_ms);  // 0 = indefinite
void      motors_stop(void);
