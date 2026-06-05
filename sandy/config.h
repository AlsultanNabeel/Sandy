
#ifndef SANDY_CONFIG_H
#define SANDY_CONFIG_H

// Pins
#define SERVO_PIN 19
#define TRIG_PIN 15
#define ECHO_PIN 13
#define BUZZER_PIN 27

// --- Base Motion (L298N) ---
// تمت إضافة هذه التعريفات هنا
#define MOTOR_LEFT_IN1  32
#define MOTOR_LEFT_IN2  33
#define MOTOR_RIGHT_IN3 25
#define MOTOR_RIGHT_IN4 26
// ملاحظة: بنات السرعة ENA/ENB غير مستخدمة حالياً للتحكم البسيط.
// يتم التحكم بالسرعة عبر الجمبر الموجود على لوحة L298N.


// Servo limits - Professional Smooth & Fast
#define SERVO_CENTER_ANGLE 90
#define SERVO_SAFE_MIN_ANGLE 5
#define SERVO_SAFE_MAX_ANGLE 175

#define SERVO_STEP_DEG 3
#define SERVO_STEP_DELAY_MS 15

// Servo pulse widths
#define SERVO_MIN_US 500
#define SERVO_MAX_US 2400

// Servo idle "head bob" — حركة خفيفة عشوائية تعطي إيحاء أن Sandy حية
#define HEAD_BOB_MIN_DEG            3
#define HEAD_BOB_MAX_DEG            8
#define HEAD_BOB_MIN_INTERVAL_MS    7000
#define HEAD_BOB_MAX_INTERVAL_MS    18000

// ── Sensors المُضافة ──
#define MIC_PIN           34   // ADC1_CH6 — input-only (مناسب لخرج MAX9814 التناظري)
#define MIC_CLAP_PEAK     2200 // عتبة amplitude لتصفيقة (0..4095 ADC)
#define MIC_COOLDOWN_MS   1500 // أقل فاصل بين تصفيقات معتبَرة

#define TOUCH_PIN         14   // TTP223 — digital out (HIGH عند اللمس). بعدنا عن 13 لأنه ECHO
#define TOUCH_DEBOUNCE_MS 80

// Distance sensor
#define DISTANCE_READ_INTERVAL_MS  1000
#define DISTANCE_PULSE_TIMEOUT_US  6000   // كان 30000UL — 6000µs = ~1m max، أسرع بـ 5x

// Face animation
#define FACE_ANIM_INTERVAL_MS 80

// Buzzer
#define ENABLE_BUZZER true
#define BUZZER_RESOLUTION 8
#define BUZZER_BASE_FREQ 1000
#define BUZZER_MAX_DURATION_MS    2000   // safety timeout للـ buzzer
#define MELODY_NOTE_GAP_MS        20     // الفجوة بين نوتات الـ melody
#define BUZZER_VOLUME             40     // 0-255: 128=أعلى صوت, 40=هادئ, 0=صامت

// Diagnostics / WiFi
#define DIAG_PRINT_INTERVAL_MS      5000
#define WIFI_RECONNECT_INTERVAL_MS  10000
#define STARTUP_WARMUP_MS           3000  // الانتظار قبل startupSequence

// Motor safety
#define MOTOR_WATCHDOG_MS           3000  // auto-stop المواتير لو ما اجا أمر جديد

// Networking (لاحقاً — مرحلة 2+3)
#define POLL_TIMEOUT_S              25
#define STATUS_POST_INTERVAL_MS     5000
#define COMMAND_ACK_TIMEOUT_MS      3000

#endif