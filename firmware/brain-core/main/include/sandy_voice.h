#pragma once
#include "esp_err.h"
#include <stdbool.h>

// Real-time voice link to the cloud (/voice WebSocket + Gemini Live).
//
// Brings up two I2S channels (INMP441 mic in, MAX98357 amp out), connects to
// the server, does the HMAC handshake, then streams mic audio up and plays
// Sandy's audio back down. Half-duplex: the mic is muted while she's talking.
//
// Call once after Wi-Fi is up. Safe to skip if the voice hardware isn't wired.
esp_err_t voice_init(void);

// True once the server accepted the handshake (auth_ok) and the link is live.
bool voice_is_connected(void);

// True while a voice conversation is in progress (wake word heard and the
// session not yet idle-closed). Other subsystems should leave the face and
// neck alone while this is true.
bool voice_session_is_active(void);
