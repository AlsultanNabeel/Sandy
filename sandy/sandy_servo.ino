// =========================
// Sandy — Servo Motion (Neck)
// =========================
// حركة بـ sine easing — slow→fast→slow بدل step ثابت
// throttle ٢٢ms لمطابقة الـ PWM cycle ٥٠Hz + writeMicroseconds للدقة الفائقة

static float       neckJourneyFrom = SERVO_CENTER_ANGLE;
static float       neckJourneyTo   = SERVO_CENTER_ANGLE;
static unsigned long neckJourneyStartMs = 0;
static unsigned long neckJourneyDurationMs = 0;
static unsigned long neckLastWriteMs = 0;
static int         neckLastWrittenUs = -1;

int clampAngle(int angle) {
  if (angle < SERVO_SAFE_MIN_ANGLE) return SERVO_SAFE_MIN_ANGLE;
  if (angle > SERVO_SAFE_MAX_ANGLE) return SERVO_SAFE_MAX_ANGLE;
  return angle;
}

void ensureServoAttached() {
  if (!neckServo.attached()) {
    neckServo.setPeriodHertz(50);
    neckServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
    delay(20);
  }
}

void moveNeckTo(int angle) {
  targetNeckAngle = clampAngle(angle);
}

// ابدأ رحلة جديدة لو targetNeckAngle اتغير
static void maybeStartNeckJourney() {
  if ((int)neckJourneyTo == targetNeckAngle) return;
  neckJourneyFrom = currentNeckAngle;
  neckJourneyTo   = targetNeckAngle;
  neckJourneyStartMs = millis();
  // مدة أبطأ شوية = ٢٠ms/درجة → حركة هادئة، أقل اهتزاز
  int distance = abs((int)neckJourneyTo - (int)neckJourneyFrom);
  neckJourneyDurationMs = constrain(distance * 20, 250, 1500);
}

// تحويل درجة (float) → ميكروثانية للـ writeMicroseconds (دقة فائقة)
static int angleToMicros(float angle) {
  if (angle < 0) angle = 0;
  if (angle > 180) angle = 180;
  return SERVO_MIN_US + (int)((angle / 180.0f) * (SERVO_MAX_US - SERVO_MIN_US));
}

void updateServoMotion() {
  maybeStartNeckJourney();
  if (neckJourneyDurationMs == 0) return;

  unsigned long now = millis();
  // throttle: PWM المرسل للسيرفو ٥٠Hz → 20ms/cycle. تحديث أسرع من هيك = اهتزاز.
  if (now - neckLastWriteMs < 22) return;
  neckLastWriteMs = now;

  unsigned long elapsed = now - neckJourneyStartMs;
  float t = (float)elapsed / (float)neckJourneyDurationMs;
  float angleF;
  if (t >= 1.0f) {
    angleF = neckJourneyTo;
    currentNeckAngle = (int)neckJourneyTo;
    neckJourneyDurationMs = 0;
  } else {
    // easeInOutSine: -(cos(πt) - 1) / 2 → ابدأ بطيء، اسرع، انتهي بطيء
    float eased = -(cosf(3.14159265f * t) - 1.0f) * 0.5f;
    angleF = neckJourneyFrom + (neckJourneyTo - neckJourneyFrom) * eased;
    currentNeckAngle = (int)angleF;
  }

  int us = angleToMicros(angleF);
  if (us != neckLastWrittenUs) {
    neckServo.writeMicroseconds(us);
    neckLastWrittenUs = us;
  }
}

void onServoAngleChange() {
  if (servoAngle < SERVO_SAFE_MIN_ANGLE) servoAngle = SERVO_SAFE_MIN_ANGLE;
  if (servoAngle > SERVO_SAFE_MAX_ANGLE) servoAngle = SERVO_SAFE_MAX_ANGLE;

  targetNeckAngle = servoAngle;
  autonomousIdle  = false;
  autonomousMode  = false;
  statusText      = "servo";

  ensureServoAttached();
  persistSaveNeckAngle(targetNeckAngle);  // احفظ في NVS — يستعيد عند إعادة التشغيل
  g_log.printf("[SERVO] target=%d current=%d attached=%d\n",
               targetNeckAngle, currentNeckAngle, neckServo.attached() ? 1 : 0);

  lastIdleActionMs = millis();
  nextIdleActionDelayMs = 30000 + random(0, 30000);
}
