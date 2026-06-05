// =========================
// Sandy — Distance Sensor (HC-SR04)
// =========================

// قراءة واحدة خام
static float _singleDistanceRead() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  unsigned long duration = pulseIn(ECHO_PIN, HIGH, DISTANCE_PULSE_TIMEOUT_US);
  if (duration == 0) return -1.0f;
  return duration * 0.0343f / 2.0f;
}

// median فلتر — ٣ قراءات → الوسطى. يلغي الـ spikes العشوائية.
float readDistanceCm() {
  float s[3];
  for (int i = 0; i < 3; i++) {
    s[i] = _singleDistanceRead();
    delay(15);
  }
  // bubble sort صغير (٣ عناصر)
  if (s[0] > s[1]) { float t = s[0]; s[0] = s[1]; s[1] = t; }
  if (s[1] > s[2]) { float t = s[1]; s[1] = s[2]; s[2] = t; }
  if (s[0] > s[1]) { float t = s[0]; s[0] = s[1]; s[1] = t; }
  return s[1];  // الوسطى
}
