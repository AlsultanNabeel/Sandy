// =========================
// Sandy — TTP223 Capacitive Touch (ربتة على الرأس)
// =========================
// TTP223 يخرج HIGH عند اللمس. نوصله بـ GPIO 14 (13 محجوز لـ ECHO).
// عند اللمس: Sandy تضحك + نغمة سعيدة.

static unsigned long g_touchLastEdgeMs = 0;
static bool          g_touchLastState  = false;

void setupTouch() {
  pinMode(TOUCH_PIN, INPUT);
  g_log.println("[TOUCH] TTP223 ready on GPIO 14");
}

static void onTouchPress() {
  g_log.println("[TOUCH] ✋ head pat");
  currentMood = MOOD_BIG_HAPPY;
  moodState = "big_happy";
  moodUntilMs = millis() + 2000;
  applyMoodMotion(currentMood);
  triggerMoodTransition();
  melodyHappy();
  autonomousIdle = true;
}

void updateTouch() {
  bool now_state = digitalRead(TOUCH_PIN) == HIGH;
  unsigned long now = millis();

  if (now_state != g_touchLastState && (now - g_touchLastEdgeMs) > TOUCH_DEBOUNCE_MS) {
    g_touchLastEdgeMs = now;
    g_touchLastState  = now_state;
    if (now_state) onTouchPress();  // فقط على الـ rising edge
  }
}
