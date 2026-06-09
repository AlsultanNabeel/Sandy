#pragma once
#include "esp_err.h"

// Minimal speaker check: plays a repeating triple-beep tone through the
// MAX98357 amp (I2S_NUM_1) so we can verify the amp + speaker wiring without
// the cloud voice stack. Gated by ENABLE_SPK_TEST in config.h — turn off once
// the speaker is verified.
esp_err_t spktest_init(void);
