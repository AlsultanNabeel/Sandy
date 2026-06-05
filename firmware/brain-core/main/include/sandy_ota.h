#pragma once
#include "esp_err.h"

esp_err_t ota_init(void);
void      ota_trigger(const char *url);   // called from MQTT handler
