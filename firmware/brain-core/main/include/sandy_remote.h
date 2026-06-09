#pragma once
#include "esp_err.h"

// Cable-free development over Wi-Fi:
//   • OTA firmware upload — POST the .bin to  http://<ip>/update
//   • Remote serial log    — stream the ESP log to a TCP client (port 3333),
//                            e.g.  nc <ip> 3333
//
// Call once after Wi-Fi is connected. Gated by ENABLE_REMOTE in config.h.
esp_err_t remote_init(void);
