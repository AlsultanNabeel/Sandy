#pragma once
#include "esp_err.h"

// Stereo INMP441 pair → sound-direction sensing. Compares the two mics and
// nudges the eyes toward whichever side is louder (face_look).
esp_err_t ears_init(void);
