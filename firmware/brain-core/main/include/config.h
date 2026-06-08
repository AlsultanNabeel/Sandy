#pragma once

// ─── Feature flags (bring-up toggles) ──────────────────────────────────────────
// 1 = subsystem enabled, 0 = skipped at boot. Bring the robot up one piece at a
// time: leave only what you've wired set to 1, reflash, test, then enable the
// next. WIFI gates the cloud parts (MQTT / OTA / voice) — they need it.
#define ENABLE_WIFI     0
#define ENABLE_FACE     1   // ST7789 display — testing this first
#define ENABLE_SERVO    0
#define ENABLE_BUZZER   0
#define ENABLE_SENSOR   0
#define ENABLE_MOTORS   0
#define ENABLE_TOUCH    0
#define ENABLE_MIC      0   // MAX9814 clap mic
#define ENABLE_EARS     1   // stereo INMP441 sound-direction sensing
#define ENABLE_OTA      0   // needs WIFI
#define ENABLE_MQTT     0   // needs WIFI
#define ENABLE_VOICE    0   // needs WIFI

// ─── GPIO Pins ────────────────────────────────────────────────────────────────
// Mapped for the ESP32-S3-DevKitC-1 / N16R8 (verified against the board's
// broken-out header). Reserved pins that are NOT used here:
//   33-37  → Octal PSRAM on the N16R8 (35/36/37 are on the header but off-limits)
//   0/3/45/46 → strapping pins
//   43/44  → UART0 console (TX/RX)
//   19/20  → native USB D-/D+
//   48     → on-board RGB LED (PIN_W2812 below)

// Servo (neck) — SG90 via LEDC PWM
#define PIN_SERVO               16

// HC-SR04 ultrasonic distance sensor
#define PIN_SENSOR_TRIG         15
#define PIN_SENSOR_ECHO         13

// Buzzer — LEDC PWM
#define PIN_BUZZER              17

// L298N motor driver
#define PIN_MOTOR_IN1           18
#define PIN_MOTOR_IN2           8
#define PIN_MOTOR_IN3           12
#define PIN_MOTOR_IN4           47

// MAX9814 analog mic (clap detection) — ADC1 CH3 = GPIO4 on the S3.
// Separate from the INMP441 voice mic below; this one only watches for claps.
#define PIN_MIC_ADC             4
#define MIC_ADC_CHANNEL         ADC_CHANNEL_3   // GPIO4 = ADC1_CH3 on S3

// TTP223 capacitive touch
#define PIN_TOUCH               14

// WS2812 RGB LED — on-board on the DevKitC-1 N16R8 (GPIO48).
#define PIN_W2812               48

// ST7789 240×240 display — SPI. Any GPIO works via the S3 GPIO matrix; these
// stay clear of the PSRAM/strapping/USB pins above.
#define PIN_TFT_MOSI            40
#define PIN_TFT_SCLK            41
#define PIN_TFT_CS              39
#define PIN_TFT_DC              42
#define PIN_TFT_RST             2
#define PIN_TFT_BLK             1    // backlight PWM
#define TFT_WIDTH               240
#define TFT_HEIGHT              240

// ─── LEDC ─────────────────────────────────────────────────────────────────────
#define LEDC_CH_SERVO           LEDC_CHANNEL_0
#define LEDC_CH_BUZZER          LEDC_CHANNEL_1
#define LEDC_TIMER_SERVO        LEDC_TIMER_0
#define LEDC_TIMER_BUZZER       LEDC_TIMER_1

// ─── Servo ────────────────────────────────────────────────────────────────────
#define SERVO_FREQ_HZ           50
#define SERVO_RESOLUTION        LEDC_TIMER_14_BIT
#define SERVO_MIN_US            500             // pulse width at 0°
#define SERVO_MAX_US            2500            // pulse width at 180°
#define SERVO_SAFE_MIN          5
#define SERVO_SAFE_MAX          175
#define SERVO_DEFAULT_POS       90

// ─── HC-SR04 ─────────────────────────────────────────────────────────────────
#define SENSOR_TIMEOUT_US       6000            // ~1 m max
#define SENSOR_MEDIAN_N         3
#define SENSOR_POLL_MS          200

// ─── Buzzer ───────────────────────────────────────────────────────────────────
#define BUZZER_RESOLUTION       LEDC_TIMER_10_BIT
#define BUZZER_VOLUME           512             // 50% of 10-bit

// ─── Motor watchdog ───────────────────────────────────────────────────────────
#define MOTOR_WATCHDOG_MS       3000

// ─── Mic (clap detection) ─────────────────────────────────────────────────────
#define MIC_SAMPLE_PERIOD_MS    5               // 200 Hz
#define MIC_CLAP_THRESHOLD      2200
#define MIC_CLAP_COOLDOWN_MS    1500

// ─── Touch ────────────────────────────────────────────────────────────────────
#define TOUCH_DEBOUNCE_MS       80

// ─── MQTT ─────────────────────────────────────────────────────────────────────
#define MQTT_STATUS_INTERVAL_MS 5000
#define MQTT_RECONNECT_MS       5000

// ─── Voice: I2S digital mic (INMP441) ──────────────────────────────────────────
#define PIN_I2S_MIC_SCK         5       // BCLK / SCK
#define PIN_I2S_MIC_WS          6       // LRCL / WS
#define PIN_I2S_MIC_SD          7       // DOUT (mic data into the S3)

// ─── Voice: I2S amplifier + speaker (MAX98357) ──────────────────────────────────
#define PIN_I2S_SPK_BCLK        9       // BCLK
#define PIN_I2S_SPK_LRC         10      // LRC / WS
#define PIN_I2S_SPK_DIN         11      // DIN (data from the S3 into the amp)

// Gemini Live: 16 kHz audio in, 24 kHz out.
#define VOICE_IN_RATE           16000
#define VOICE_OUT_RATE          24000
// Down-shift for the 32-bit INMP441 sample; also acts as gain. Tune on hardware.
#define VOICE_MIC_GAIN_SHIFT    14
// Keep the mic muted this long after Sandy's last audio (avoids echo).
#define VOICE_HALF_DUPLEX_TAIL_MS  400
