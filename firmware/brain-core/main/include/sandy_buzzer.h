#pragma once
#include "sandy_types.h"
#include "esp_err.h"

esp_err_t buzzer_init(void);
void      buzzer_play(sandy_melody_t melody);
void      buzzer_stop(void);
