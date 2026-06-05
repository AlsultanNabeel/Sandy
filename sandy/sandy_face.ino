// =========================
// Sandy — Mood / Face / Idle Behavior
// =========================

// ── انتقال المود: blink قسري ١٥٠ms يخفي تغير الفم/الحواجب ──
void triggerMoodTransition() {
  blinkUntilMs = millis() + 180;
  lastBlinkMs = millis();
}

// ── Head bob — حركة خفيفة عشوائية أثناء idle (تعطي إيحاء حي) ──
static unsigned long g_nextBobMs = 0;
void updateIdleHeadBob() {
  if (!autonomousIdle) return;
  unsigned long now = millis();
  if (now < g_nextBobMs) return;
  // المقدار: ٣-٨ درجات، الاتجاه عشوائي
  int mag = random(HEAD_BOB_MIN_DEG, HEAD_BOB_MAX_DEG + 1);
  int sign = (random(0, 2) == 0) ? -1 : 1;
  moveNeckTo(currentNeckAngle + sign * mag);
  g_nextBobMs = now + random(HEAD_BOB_MIN_INTERVAL_MS, HEAD_BOB_MAX_INTERVAL_MS);
}

const char* moodToString(int mood) {
  switch (mood) {
    case MOOD_IDLE: return "idle";
    case MOOD_HAPPY: return "happy";
    case MOOD_BIG_HAPPY: return "big_happy";
    case MOOD_CURIOUS: return "curious";
    case MOOD_THINK: return "think";
    case MOOD_TALK: return "talk";
    case MOOD_ALERT: return "alert";
    case MOOD_SURPRISED: return "surprised";
    case MOOD_SLEEPY: return "sleepy";
    case MOOD_BORED: return "bored";
    case MOOD_YAWN: return "yawn";
    case MOOD_SAD: return "sad";
    case MOOD_ANGRY: return "angry";
    case MOOD_SMIRK: return "smirk";
    case MOOD_CUTE: return "cute";
    case MOOD_EXCITED: return "excited";
    case MOOD_SHY: return "shy";
    case MOOD_CONFUSED: return "confused";
    case MOOD_EMPATHETIC: return "empathetic";
    case MOOD_LOVE: return "love";
    case MOOD_CRY: return "cry";
    case MOOD_WINK: return "wink";
    case MOOD_KISS: return "kiss";
    case MOOD_HEART_EYES: return "heart_eyes";
    case MOOD_CALM: return "calm";
    case MOOD_ASLEEP: return "asleep";
    default: return "idle";
  }
}

// ── Mood → Neck Servo ────────────────────────────────────────────
void applyMoodMotion(int mood) {
  switch (mood) {
    case MOOD_IDLE:       moveNeckTo(SERVO_CENTER_ANGLE); break;
    case MOOD_CALM:       moveNeckTo(SERVO_CENTER_ANGLE - 2); break;
    case MOOD_HAPPY:      moveNeckTo(SERVO_CENTER_ANGLE + 6); break;
    case MOOD_BIG_HAPPY:  moveNeckTo(SERVO_CENTER_ANGLE - 8); break;
    case MOOD_CURIOUS:    moveNeckTo(SERVO_CENTER_ANGLE + 16); break;
    case MOOD_THINK:      moveNeckTo(SERVO_CENTER_ANGLE - 12); break;
    case MOOD_SURPRISED:  moveNeckTo(SERVO_CENTER_ANGLE + 2); break;
    case MOOD_SLEEPY:     moveNeckTo(SERVO_CENTER_ANGLE - 8); break;
    case MOOD_BORED:      moveNeckTo(SERVO_CENTER_ANGLE + 3); break;
    case MOOD_YAWN:       moveNeckTo(SERVO_CENTER_ANGLE - 10); break;
    case MOOD_SAD:        moveNeckTo(SERVO_CENTER_ANGLE - 12); break;
    case MOOD_ANGRY:      moveNeckTo(SERVO_CENTER_ANGLE + 18); break;
    case MOOD_SMIRK:      moveNeckTo(SERVO_CENTER_ANGLE + 10); break;
    case MOOD_CUTE:       moveNeckTo(SERVO_CENTER_ANGLE - 5); break;
    case MOOD_EXCITED:    moveNeckTo(SERVO_CENTER_ANGLE + random(-12, 13)); break;
    case MOOD_SHY:        moveNeckTo(SERVO_CENTER_ANGLE - 10); break;
    case MOOD_CONFUSED:   moveNeckTo(SERVO_CENTER_ANGLE + 8); break;
    case MOOD_EMPATHETIC: moveNeckTo(SERVO_CENTER_ANGLE - 8); break;
    case MOOD_LOVE:       moveNeckTo(SERVO_CENTER_ANGLE - 4); break;
    case MOOD_CRY:        moveNeckTo(SERVO_CENTER_ANGLE - 16); break;
    case MOOD_WINK:       moveNeckTo(SERVO_CENTER_ANGLE + 4); break;
    case MOOD_KISS:       moveNeckTo(SERVO_CENTER_ANGLE - 6); break;
    case MOOD_HEART_EYES: moveNeckTo(SERVO_CENTER_ANGLE - 2); break;
    case MOOD_ALERT:      moveNeckTo(SERVO_CENTER_ANGLE + 14); break;
  }
}

// ── Idle behavior ────────────────────────────────────────────────
void chooseNewEyeTarget() {
  switch (currentMood) {
    case MOOD_SLEEPY:
    case MOOD_YAWN:
      targetEyeOffsetX = random(-1, 2);
      targetEyeOffsetY = random(1, 3);
      break;
    case MOOD_CURIOUS:
    case MOOD_CONFUSED:
      targetEyeOffsetX = random(-7, 8);
      targetEyeOffsetY = random(-2, 3);
      break;
    default:
      targetEyeOffsetX = random(-5, 6);
      targetEyeOffsetY = random(-3, 4);
      break;
  }
}

void runIdleAction() {
  int r = random(0, 100);

  if      (r < 16) currentMood = MOOD_CURIOUS;
  else if (r < 28) currentMood = MOOD_BORED;
  else if (r < 38) currentMood = MOOD_SLEEPY;
  else if (r < 50) currentMood = MOOD_HAPPY;
  else if (r < 58) currentMood = MOOD_SMIRK;
  else if (r < 66) currentMood = MOOD_SHY;
  else if (r < 74) currentMood = MOOD_CONFUSED;
  else if (r < 82) currentMood = MOOD_CUTE;
  else if (r < 88) currentMood = MOOD_CALM;
  else if (r < 93) currentMood = MOOD_WINK;
  else if (r < 97) currentMood = MOOD_BIG_HAPPY;
  else             currentMood = MOOD_IDLE;

  moodUntilMs = millis() + 1200 + random(0, 1200);
  fxStartMs = millis();
  // ملاحظة: ما نحرك الرقبة هنا — head bobs العشوائية وحدها كافية
  moodState = moodToString(currentMood);

  nextIdleActionDelayMs = 180000 + random(0, 120000);
}

void updateFaceAnimation() {
  unsigned long now = millis();

  if (currentMood == MOOD_TALK || currentMood == MOOD_EXCITED ||
      currentMood == MOOD_CRY  || currentMood == MOOD_LOVE) {
    if (now - lastTalkFrameMs > 75) {
      lastTalkFrameMs = now;
      talkFrame = (talkFrame + 1) % 6;
      talkingPulse = !talkingPulse;
    }
  }

  if (now - lastBlinkMs > 1800 + random(0, 2200)) {
    blinkUntilMs = now + 80;
    lastBlinkMs = now;
  }

  if (now - lastEyeTargetMs > 520) {
    lastEyeTargetMs = now;
    chooseNewEyeTarget();
  }

  if (eyeOffsetX < targetEyeOffsetX) eyeOffsetX++;
  else if (eyeOffsetX > targetEyeOffsetX) eyeOffsetX--;

  if (eyeOffsetY < targetEyeOffsetY) eyeOffsetY++;
  else if (eyeOffsetY > targetEyeOffsetY) eyeOffsetY--;

  if (autonomousIdle) {
    if (moodUntilMs > 0 && now > moodUntilMs) {
      currentMood = MOOD_IDLE;
      moodUntilMs = 0;
      // ما نرجع الرقبة للمركز هنا — تخليها بمكانها الحالي
      moodState = "idle";
    }

    if (now - lastIdleActionMs > nextIdleActionDelayMs) {
      lastIdleActionMs = now;
      runIdleAction();
    }
  }
  if (currentMood == MOOD_YAWN || currentMood == MOOD_ASLEEP) {
    zzz_phase = (zzz_phase + 1) % 100;
  } else {
    zzz_phase = 0;
  }
  if (currentMood == MOOD_EMPATHETIC) {
    tear_wobble = (millis() / 150) % 2 == 0 ? 1 : -1;
  } else {
    tear_wobble = 0;
  }
  if (currentMood == MOOD_CRY) {
    tear_fall_phase = (tear_fall_phase + 3) % 100;
  } else {
    tear_fall_phase = 0;
  }

  drawFace();
}

// ── Startup Sequence — non-blocking state machine ────────────────
static int           g_startupStage  = 0;
static unsigned long g_startupNextMs = 0;

void updateStartupSequence() {
  if (g_startupStage >= 3) return;
  unsigned long now = millis();
  if (now < g_startupNextMs) return;

  // ملاحظة: لا نحرّك الرقبة أثناء البوت — تبقى بالزاوية المحفوظة من NVS
  switch (g_startupStage) {
    case 0:
      fxStartMs = now;
      currentMood = MOOD_HAPPY;
      drawFace();
      melodyBoot();
      g_startupStage = 1;
      g_startupNextMs = now + 250;
      break;
    case 1:
      currentMood = MOOD_BIG_HAPPY;
      drawFace();
      g_startupStage = 2;
      g_startupNextMs = now + 250;
      break;
    case 2:
      currentMood = MOOD_IDLE;
      moodState = "idle";
      drawFace();
      g_startupStage = 3;
      break;
  }
}

bool isStartupSequenceDone() {
  return g_startupStage >= 3;
}

// ── Cloud Callbacks (Mood + Autonomous) ──────────────────────────
void onMoodStateChange() {
  if      (moodState == "idle")        currentMood = MOOD_IDLE;
  else if (moodState == "happy")       currentMood = MOOD_HAPPY;
  else if (moodState == "big_happy")   currentMood = MOOD_BIG_HAPPY;
  else if (moodState == "curious")     currentMood = MOOD_CURIOUS;
  else if (moodState == "think")       currentMood = MOOD_THINK;
  else if (moodState == "talk")        currentMood = MOOD_TALK;
  else if (moodState == "alert")       currentMood = MOOD_ALERT;
  else if (moodState == "surprised")   currentMood = MOOD_SURPRISED;
  else if (moodState == "sleepy")      currentMood = MOOD_SLEEPY;
  else if (moodState == "bored")       currentMood = MOOD_BORED;
  else if (moodState == "yawn")        currentMood = MOOD_YAWN;
  else if (moodState == "sad")         currentMood = MOOD_SAD;
  else if (moodState == "angry")       currentMood = MOOD_ANGRY;
  else if (moodState == "smirk")       currentMood = MOOD_SMIRK;
  else if (moodState == "cute")        currentMood = MOOD_CUTE;
  else if (moodState == "excited")     currentMood = MOOD_EXCITED;
  else if (moodState == "shy")         currentMood = MOOD_SHY;
  else if (moodState == "confused")    currentMood = MOOD_CONFUSED;
  else if (moodState == "empathetic")  currentMood = MOOD_EMPATHETIC;
  else if (moodState == "love")        currentMood = MOOD_LOVE;
  else if (moodState == "cry")         currentMood = MOOD_CRY;
  else if (moodState == "wink")        currentMood = MOOD_WINK;
  else if (moodState == "kiss")        currentMood = MOOD_KISS;
  else if (moodState == "heart_eyes")  currentMood = MOOD_HEART_EYES;
  else if (moodState == "calm")        currentMood = MOOD_CALM;
  else if (moodState == "asleep")      currentMood = MOOD_ASLEEP;
  else return;

  fxStartMs = millis();
  autonomousIdle = false;
  autonomousMode = false;
  statusText = "mood:" + moodState;

  lastIdleActionMs = millis();
  nextIdleActionDelayMs = 30000 + random(0, 30000);

  triggerMoodTransition();
  if (currentMood == MOOD_KISS) spawnKissHearts();

  drawFace();
}

void onAutonomousModeChange() {
  autonomousIdle = autonomousMode;
  statusText = autonomousIdle ? "autonomous on" : "autonomous off";
}
