#pragma once
#include "esp_err.h"
#include <stdbool.h>

esp_err_t wifi_sandy_start(void);
bool      wifi_sandy_is_connected(void);
