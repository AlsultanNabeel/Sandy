#pragma once
#include "esp_err.h"

esp_err_t mqtt_sandy_start(void);
void      mqtt_publish_status(void);    // call manually if needed; auto every 5s
