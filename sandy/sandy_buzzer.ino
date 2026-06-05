// =========================
// Sandy — Buzzer + Async Melodies
// =========================
// playMelody يستخدم state machine في loop() — لا blocking
// المصفوفات `static const` ليعيش الـ pointer بأمان

void setupBuzzer() {
  if (!ENABLE_BUZZER) {
    Serial.println("Buzzer disabled in config");
    return;
  }

  buzzerReady = buzzerLedcAttachCompat();

  Serial.print("BUZZER_PIN = ");
  Serial.println(BUZZER_PIN);
  Serial.print("buzzerReady = ");
  Serial.println(buzzerReady ? "true" : "false");

  if (buzzerReady) {
    buzzerLedcWriteToneCompat(0);
    buzzerLedcWriteDutyCompat(0);
  }
}

void stopBuzzer() {
  if (!ENABLE_BUZZER) return;

  buzzerLedcWriteToneCompat(0);
  buzzerLedcWriteDutyCompat(0);
  buzzerLedcDetachCompat();

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  buzzerReady = false;
}

// ── Async Melody State Machine ───────────────────────────────────
enum MelodyPhase {
  MELODY_IDLE,
  MELODY_PLAYING_NOTE,
  MELODY_NOTE_GAP
};

struct MelodyState {
  MelodyPhase phase = MELODY_IDLE;
  const int* notes = nullptr;
  const int* durs = nullptr;
  int len = 0;
  int idx = 0;
  unsigned long phaseUntilMs = 0;
};

static MelodyState g_melody;
// MELODY_NOTE_GAP_MS مُعرّف في config.h

// ضمان attach للـ LEDC قبل كل نوتة (مطلوب بعد stopBuzzer)
static bool ensureBuzzerAttached() {
  if (buzzerReady) return true;
  buzzerReady = buzzerLedcAttachCompat();
  return buzzerReady;
}

static void startMelodyAsync(const int* notes, const int* durs, int len) {
  if (!ENABLE_BUZZER || len <= 0) return;
  if (!ensureBuzzerAttached()) return;

  g_melody.notes = notes;
  g_melody.durs = durs;
  g_melody.len = len;
  g_melody.idx = 0;

  int freq = notes[0];
  if (freq > 0) {
    buzzerLedcWriteToneCompat(freq);
  } else {
    stopBuzzer();
  }
  g_melody.phase = MELODY_PLAYING_NOTE;
  g_melody.phaseUntilMs = millis() + durs[0];
}

void updateMelody() {
  if (g_melody.phase == MELODY_IDLE) return;
  unsigned long now = millis();
  if (now < g_melody.phaseUntilMs) return;

  if (g_melody.phase == MELODY_PLAYING_NOTE) {
    // النوتة انتهت — stopBuzzer (detach كامل، نفس سلوك الكود الأصلي)
    stopBuzzer();
    g_melody.phase = MELODY_NOTE_GAP;
    g_melody.phaseUntilMs = now + MELODY_NOTE_GAP_MS;
    return;
  }

  // MELODY_NOTE_GAP: الفجوة انتهت — انتقل للنوتة التالية
  g_melody.idx++;
  if (g_melody.idx >= g_melody.len) {
    g_melody.phase = MELODY_IDLE;
    return;
  }

  // إعادة attach للنوتة الجديدة
  if (!ensureBuzzerAttached()) {
    g_melody.phase = MELODY_IDLE;
    return;
  }

  int freq = g_melody.notes[g_melody.idx];
  if (freq > 0) {
    buzzerLedcWriteToneCompat(freq);
  } else {
    stopBuzzer();
  }
  g_melody.phase = MELODY_PLAYING_NOTE;
  g_melody.phaseUntilMs = now + g_melody.durs[g_melody.idx];
}

bool isMelodyPlaying() {
  return g_melody.phase != MELODY_IDLE;
}

// playToneMs — legacy blocking helper (يُستخدم من buzzerSelfTest فقط)
void playToneMs(int freq, int dur) {
  if (!ENABLE_BUZZER) return;

  if (!buzzerReady) {
    buzzerReady = buzzerLedcAttachCompat();
    if (!buzzerReady) return;
  }

  if (freq <= 0) {
    stopBuzzer();
    delay(dur);
    return;
  }

  buzzerLedcWriteToneCompat(freq);
  delay(dur);
  stopBuzzer();
  delay(20);
}

void playMelody(const int *notes, const int *durs, int len) {
  if (!ENABLE_BUZZER) return;
  startMelodyAsync(notes, durs, len);
}

// ── مكتبة النغمات ────────────────────────────────────────────────

void melodyBoot() {
  static const int n[] = {880, 1047, 1319, 1760};
  static const int d[] = {70, 70, 70, 200};
  playMelody(n, d, 4);
}

void melodyHappy() {
  static const int n[] = {880, 988, 1109, 1319};
  static const int d[] = {80, 80, 80, 180};
  playMelody(n, d, 4);
}

void melodyCurious() {
  static const int n[] = {880, 1109};
  static const int d[] = {120, 250};
  playMelody(n, d, 2);
}

void melodySad() {
  static const int n[] = {880, 784, 659};
  static const int d[] = {250, 200, 400};
  playMelody(n, d, 3);
}

void melodyAlert() {
  static const int n[] = {2093, 0, 2093};
  static const int d[] = {150, 80, 150};
  playMelody(n, d, 3);
}

void melodyError() {
  static const int n[] = {523, 494, 466, 440};
  static const int d[] = {100, 100, 100, 200};
  playMelody(n, d, 4);
}

void buzzerSelfTest() {
  Serial.println("BUZZER SELF TEST START");

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, HIGH);
  delay(400);
  digitalWrite(BUZZER_PIN, LOW);
  delay(300);

  if (ENABLE_BUZZER && buzzerReady) {
    ledcWriteTone(BUZZER_PIN, 1500);
    delay(400);
    ledcWriteTone(BUZZER_PIN, 0);
    delay(300);

    ledcWriteTone(BUZZER_PIN, 2200);
    delay(400);
    ledcWriteTone(BUZZER_PIN, 0);
    delay(300);
  }

  Serial.print("ENABLE_BUZZER = ");
  Serial.println(ENABLE_BUZZER ? "true" : "false");
  Serial.print("buzzerReady = ");
  Serial.println(buzzerReady ? "true" : "false");
  Serial.println("BUZZER SELF TEST END");
}

void maybeTalkTone() {
  return;
}

void playEventSound(const String& eventName) {
  if      (eventName == "startup") melodyBoot();
  else if (eventName == "wake")    melodyHappy();
  else if (eventName == "sleep")   melodyCurious();
  else if (eventName == "alert")   melodyAlert();
  else if (eventName == "sad")     melodySad();
  else if (eventName == "error")   melodyError();
  else if (eventName == "stop")    stopBuzzer();
}

void onBuzzerCommandChange() {
  g_log.print("[CMD] Buzzer: ");
  g_log.println(buzzerCommand);

  // نخزن الأمر ونرفع علماً لتشغيله في الـ loop الرئيسي
  pendingBuzzerEvent = buzzerCommand;
  pendingBuzzerPlay = true;

  // نضبط مؤقتاً لإيقاف البازر إذا استمر بالخطأ
  eventBuzzerActive = true;
  eventBuzzerUntilMs = millis() + BUZZER_MAX_DURATION_MS;
  statusText = "buzzer:" + buzzerCommand;

  // إعادة تعيين المتغير فوراً
  buzzerCommand = "none";
}
