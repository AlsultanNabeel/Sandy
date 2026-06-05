// =========================
// Sandy — Forward declarations عبر الملفات
// =========================
// كل دالة تُستدعى من ملف .ino مختلف عن مكان تعريفها لازم تكون هنا.

#ifndef SANDY_PROTOS_H
#define SANDY_PROTOS_H

// ── Cross-file globals ────────────────────────────────────────────
extern bool g_networkServicesStarted;
extern uint32_t g_wifiDropCount;

// ── sandy_servo.ino ───────────────────────────────────────────────
void   onServoAngleChange();
int    clampAngle(int angle);
void   ensureServoAttached();
void   moveNeckTo(int angle);
void   updateServoMotion();

// ── sandy_buzzer.ino ──────────────────────────────────────────────
void setupBuzzer();
void stopBuzzer();
void updateMelody();
bool isMelodyPlaying();
void playMelody(const int *notes, const int *durs, int len);
void melodyBoot();
void melodyHappy();
void melodyCurious();
void melodySad();
void melodyAlert();
void melodyError();
void playEventSound(const String& eventName);
void onBuzzerCommandChange();
void maybeTalkTone();

// ── sandy_motors.ino ──────────────────────────────────────────────
void stopMotors();
void moveForward();
void moveBackward();
void turnLeft();
void turnRight();
void checkMotorWatchdog();
void onBaseActionChange();

// ── sandy_mqtt.ino ────────────────────────────────────────────────
void setupMQTT();
void updateMQTT();

// ── sandy_ota.ino ─────────────────────────────────────────────────
void updateTelnet();
void startNetworkServicesIfReady();

// ── sandy_wifi.ino ────────────────────────────────────────────────
void connectWiFi();
void ensureWiFiConnected();
void logResetReason();
void logDiagnostics();
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info);

// ── sandy_face.ino ────────────────────────────────────────────────
const char* moodToString(int mood);
void applyMoodMotion(int mood);
void updateFaceAnimation();
void updateStartupSequence();
bool isStartupSequenceDone();
void onMoodStateChange();
void onAutonomousModeChange();
void triggerMoodTransition();
void updateIdleHeadBob();
// spawnKissHearts() مُعرَّفة في sandy-faces.h (static inline) — لا prototype هنا

// ── sandy_sensor.ino ──────────────────────────────────────────────
float readDistanceCm();

// ── sandy_mic.ino (MAX9814 — analog mic) ──────────────────────────
void setupMic();
void updateMic();

// ── sandy_touch.ino (TTP223 — capacitive head pad) ────────────────
void setupTouch();
void updateTouch();

// ── sandy_persist.ino (NVS / Preferences) ─────────────────────────
void persistInit();
int  persistLoadNeckAngle(int fallback);
void persistSaveNeckAngle(int angle);

#endif
