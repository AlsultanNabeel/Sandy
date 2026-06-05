// =========================
// Sandy — MAX9814 Microphone (Clap Detection)
// =========================
// MAX9814 يخرج جهد تناظري متناسب مع مستوى الصوت.
// نقرأه عبر ADC1 (GPIO 34 — input-only، مناسب جداً)، ونكشف peak amplitude.
// تصفيقة = peak عابر للعتبة + cooldown.

static unsigned long g_micLastClapMs = 0;

void setupMic() {
  // GPIO 34 input-only — لا pinMode مطلوب لـ ADC1
  analogReadResolution(12);          // 0..4095
  analogSetPinAttenuation(MIC_PIN, ADC_11db);  // أعلى range (~3.3V full-scale)
  g_log.println("[MIC] MAX9814 ready on GPIO 34");
}

// onClap — رد فعل Sandy عند تصفيقة معتبَرة
static void onClap() {
  g_log.println("[MIC] 👏 clap detected");
  currentMood = MOOD_SURPRISED;
  moodState = "surprised";
  moodUntilMs = millis() + 1500;
  applyMoodMotion(currentMood);
  triggerMoodTransition();
  melodyHappy();
  autonomousIdle = true;
}

void updateMic() {
  static unsigned long lastReadMs = 0;
  unsigned long now = millis();
  if (now - lastReadMs < 5) return;  // 200Hz كافي لكشف تصفيق
  lastReadMs = now;

  int v = analogRead(MIC_PIN);
  // amplitude النسبي من المركز (~1650 = 1.65V بـ AC coupling)
  int amp = abs(v - 1650);

  if (amp > MIC_CLAP_PEAK && (now - g_micLastClapMs) > MIC_COOLDOWN_MS) {
    g_micLastClapMs = now;
    onClap();
  }
}
