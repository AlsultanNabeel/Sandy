#pragma once

// ─── GPIO Pins ────────────────────────────────────────────────────────────────
// Kept from original Arduino firmware where S3 is compatible.
// Pins marked ⛏️ need verification against your physical N16R8 board.

// Servo (neck) — SG90 via LEDC PWM
#define PIN_SERVO               19

// HC-SR04 ultrasonic distance sensor
#define PIN_SENSOR_TRIG         15
#define PIN_SENSOR_ECHO         13

// Buzzer — LEDC PWM
#define PIN_BUZZER              27

// L298N motor driver
#define PIN_MOTOR_IN1           32
#define PIN_MOTOR_IN2           33
#define PIN_MOTOR_IN3           25
#define PIN_MOTOR_IN4           26

// MAX9814 mic — ADC1 CH3 on ESP32-S3 (GPIO4)
// ⛏️ Original was GPIO34 (ESP32 ADC only). S3 ADC1: GPIO1-10.
#define PIN_MIC_ADC             4
#define MIC_ADC_CHANNEL         ADC_CHANNEL_3   // GPIO4 = ADC1_CH3 on S3

// TTP223 capacitive touch
#define PIN_TOUCH               14

// W2812 RGB LED — built-in on N16R8 DevKitC
// ⛏️ Commonly GPIO48 on N16R8; verify with your board silkscreen
#define PIN_W2812               48

// ST7789 240×240 display — SPI2 (non-conflicting with other peripherals)
// ⛏️ Adjust to match your wiring once board arrives
#define PIN_TFT_MOSI            35
#define PIN_TFT_SCLK            36
#define PIN_TFT_CS              21
#define PIN_TFT_DC              37
#define PIN_TFT_RST             38
#define PIN_TFT_BLK             39   // backlight PWM
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
