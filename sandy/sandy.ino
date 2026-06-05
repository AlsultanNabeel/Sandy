// =========================
// Sandy — Main (setup + loop + globals)
// =========================
// الكود مُقسم على ملفات .ino متعددة. Arduino IDE يدمجها في translation unit واحد.
//   sandy.ino         — globals + setup + loop  (هذا الملف)
//   sandy_buzzer.ino  — البازر + النغمات الـ async
//   sandy_face.ino    — المود + الوجه + التصرف التلقائي
//   sandy_motors.ino  — مواتير القاعدة (L298N)
//   sandy_mqtt.ino    — MQTT (HiveMQ)
//   sandy_ota.ino     — OTA + Telnet
//   sandy_sensor.ino  — قراءة المسافة (HC-SR04)
//   sandy_servo.ino   — حركة الرقبة (sine easing)
//   sandy_wifi.ino    — WiFi + diagnostics

#include <Arduino.h>
#include <TFT_eSPI.h>
#include <ESP32Servo.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <ArduinoOTA.h>
#include <PubSubClient.h>
#include "esp_system.h"
#include "esp_task_wdt.h"
#include "config.h"
#include "secrets.h"
#include "sandy_protos.h"

// Personal photo display (Phase 8 PoC). Generate via:
//   python tools/photo_to_rgb565.py <your_photo.jpg>
// File is gitignored for privacy — anyone cloning the repo needs to create their own.
#if __has_include("me_photo.h")
  #include "me_photo.h"
  #define SANDY_HAS_PHOTO 1
#endif

// ===== LEDC compatibility (ESP32 core 3.x) =====
static bool buzzerLedcAttachCompat() {
  return ledcAttach(BUZZER_PIN, BUZZER_BASE_FREQ, BUZZER_RESOLUTION);
}

static void buzzerLedcDetachCompat() {
  ledcDetach(BUZZER_PIN);
}

static void buzzerLedcWriteToneCompat(int freq) {
  ledcWriteTone(BUZZER_PIN, freq);
  if (freq > 0) {
    ledcWrite(BUZZER_PIN, BUZZER_VOLUME);  // override default 50% duty للتحكم بالصوت
  }
}

static void buzzerLedcWriteDutyCompat(uint32_t duty) {
  ledcWrite(BUZZER_PIN, duty);
}


TFT_eSPI tft = TFT_eSPI();
Servo neckServo;

// =========================
// Telnet Serial Mirror — global حتى يصل sandy_ota.ino
// =========================
WiFiServer g_telnetServer(23);
WiFiClient g_telnetClient;

class MirrorStream : public Print {
 public:
  size_t write(uint8_t c) override {
    Serial.write(c);
    if (g_telnetClient && g_telnetClient.connected()) {
      g_telnetClient.write(c);
    }
    return 1;
  }
  size_t write(const uint8_t* buf, size_t n) override {
    Serial.write(buf, n);
    if (g_telnetClient && g_telnetClient.connected()) {
      g_telnetClient.write(buf, n);
    }
    return n;
  }
};
MirrorStream g_log;

// ── State variables (سابقاً كانت Arduino Cloud properties) ──────
String baseAction      = "stop";     // forward / backward / left / right / stop
String buzzerCommand   = "none";     // startup / wake / alert / sad / error / stop / none
String moodState       = "idle";     // idle / happy / curious / talk / alert / ...
int    servoAngle      = 0;          // زاوية الرقبة — تُعاد ضبطها في setup
bool   autonomousMode  = true;       // الوضع التلقائي

String statusText      = "booted";   // النص اللي Sandy تخبر عنه
float  distanceCm      = 0;          // قراءة حساس المسافة

bool eventBuzzerActive = false;
unsigned long eventBuzzerUntilMs = 0;
String pendingBuzzerEvent = "";
bool pendingBuzzerPlay = false;

// Cross-file globals (تُعرَّف هنا حتى تُرى من كل ملفات .ino اللاحقة)
bool g_networkServicesStarted = false;
uint32_t g_wifiDropCount = 0;

// =========================
// Mood System
// =========================
enum Mood {
  MOOD_IDLE = 0,
  MOOD_HAPPY,
  MOOD_BIG_HAPPY,
  MOOD_CURIOUS,
  MOOD_THINK,
  MOOD_TALK,
  MOOD_ALERT,
  MOOD_SURPRISED,
  MOOD_SLEEPY,
  MOOD_BORED,
  MOOD_YAWN,
  MOOD_SAD,
  MOOD_ANGRY,
  MOOD_SMIRK,
  MOOD_CUTE,
  MOOD_EXCITED,
  MOOD_SHY,
  MOOD_CONFUSED,
  MOOD_EMPATHETIC,
  MOOD_LOVE,
  MOOD_CRY,
  MOOD_WINK,
  MOOD_KISS,
  MOOD_HEART_EYES,
  MOOD_CALM,
  MOOD_ASLEEP
};

Mood currentMood = MOOD_IDLE;

unsigned long lastAnimMs = 0;
unsigned long lastBlinkMs = 0;
unsigned long blinkUntilMs = 0;
unsigned long moodUntilMs = 0;
unsigned long lastIdleActionMs = 0;
unsigned long nextIdleActionDelayMs = 180000 + random(0, 120000);
unsigned long lastEyeTargetMs = 0;
unsigned long startupWarmupUntilMs = 0;
bool startupSequenceDone = false;

bool autonomousIdle = true;
bool talkingPulse = false;
uint8_t talkFrame = 0;
unsigned long lastTalkFrameMs = 0;
unsigned long fxStartMs = 0;

// =========================
// Eyes
// =========================
int eyeOffsetX = 0;
int eyeOffsetY = 0;
int targetEyeOffsetX = 0;
int targetEyeOffsetY = 0;

#include "sandy-faces.h"

// =========================
// Servo + Buzzer state vars (المنطق في sandy_servo.ino / sandy_buzzer.ino)
// =========================
int currentNeckAngle = SERVO_CENTER_ANGLE;
int targetNeckAngle  = SERVO_CENTER_ANGLE;

bool buzzerReady = false;

// =========================
// Setup / Loop
// =========================

void setup() {
  Serial.begin(115200);
  delay(200);
  logResetReason();
  WiFi.onEvent(onWiFiEvent);
  randomSeed(esp_random());

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  digitalWrite(TRIG_PIN, LOW);
  tft.init();
  tft.setRotation(0);
  clearFace();

  // ── شعار البوت أثناء التهيئة ──
  ensureFaceSprite();
  for (int p = 0; p <= 100; p += 10) {
    drawBootLogo(faceSprite, p);
    faceSprite.pushSprite(10, 10);
    delay(40);
  }

  setupBuzzer();
  stopBuzzer();

  pinMode(MOTOR_LEFT_IN1, OUTPUT);
  pinMode(MOTOR_LEFT_IN2, OUTPUT);
  pinMode(MOTOR_RIGHT_IN3, OUTPUT);
  pinMode(MOTOR_RIGHT_IN4, OUTPUT);
  stopMotors();

  setupMic();
  setupTouch();
  persistInit();

  connectWiFi();

  // pre-allocate String capacity (يمنع heap fragmentation طول الأمد)
  moodState.reserve(16);
  buzzerCommand.reserve(16);
  baseAction.reserve(16);
  statusText.reserve(32);
  pendingBuzzerEvent.reserve(16);

  moodState     = "idle";
  buzzerCommand = "none";
  baseAction    = "stop";
  statusText    = "booted";
  autonomousMode = true;
  distanceCm    = 0;

  // استرجع آخر زاوية محفوظة
  int restoredAngle = persistLoadNeckAngle(SERVO_CENTER_ANGLE);
  servoAngle = restoredAngle;
  ensureServoAttached();
  currentNeckAngle = restoredAngle;
  targetNeckAngle  = restoredAngle;
  neckServo.write(currentNeckAngle);

  startupWarmupUntilMs = millis() + STARTUP_WARMUP_MS;
  startupSequenceDone = false;

  autonomousIdle = true;
  currentMood = MOOD_IDLE;
  fxStartMs = millis();
  lastIdleActionMs = millis();
  nextIdleActionDelayMs = 180000 + random(0, 120000);
  drawFace();

  // Watchdog Timer — لو اللوب علق >5s، reset تلقائي
  esp_task_wdt_config_t wdt_cfg = {
    .timeout_ms = 5000,
    .idle_core_mask = 0,
    .trigger_panic = true,
  };
  esp_task_wdt_init(&wdt_cfg);
  esp_task_wdt_add(NULL);
}


void loop() {
  unsigned long now = millis();
  esp_task_wdt_reset();

  ensureWiFiConnected();

  // بدء OTA + Telnet + MQTT لمّا WiFi يصير UP (مرة وحدة)
  startNetworkServicesIfReady();

  if (g_networkServicesStarted) {
    ArduinoOTA.handle();
    updateTelnet();
    updateMQTT();
  }

  // تقدّم الـ melody async (state machine — لا blocking)
  updateMelody();

  // safety watchdog للمواتير — auto-stop لو ما اجا أمر جديد
  checkMotorWatchdog();

  // معالجة نغمات البازر المجدولة
  if (pendingBuzzerPlay) {
    pendingBuzzerPlay = false;
    playEventSound(pendingBuzzerEvent);
    pendingBuzzerEvent = "";
  }

  // تسلسل بدء التشغيل (non-blocking state machine)
  if (!startupSequenceDone && now >= startupWarmupUntilMs) {
    updateStartupSequence();
    if (isStartupSequenceDone()) {
      startupSequenceDone = true;
    }
  }

  // تحديث تحريك الوجه
  if (now - lastAnimMs > FACE_ANIM_INTERVAL_MS) {
    lastAnimMs = now;
    updateFaceAnimation();
  }

  // معالجة حالة الكلام (الحركة والرقبة)
  if (currentMood == MOOD_TALK) {
    maybeTalkTone();
    applyMoodMotion(MOOD_TALK);
  }

  // إيقاف البازر السحابي بعد انتهاء الوقت
  if (eventBuzzerActive && now >= eventBuzzerUntilMs) {
    stopBuzzer();
    eventBuzzerActive = false;
  }

  // تحديث حركة السيرفو (الرقبة) + head bobs العشوائية في الـ idle
  updateIdleHeadBob();
  updateServoMotion();

  // الميكروفون (MAX9814) + اللمس (TTP223)
  updateMic();
  updateTouch();

  // قراءة مستشعر المسافة وتحديث القيمة
  static unsigned long lastDistanceReadMs = 0;
  if (now - lastDistanceReadMs >= DISTANCE_READ_INTERVAL_MS) {
    lastDistanceReadMs = now;

    float d = readDistanceCm();
    if (d > 0) {
      distanceCm = d;
    }
  }

  logDiagnostics();
}
