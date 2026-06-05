// =========================
// Sandy — NVS Persistence (Preferences API)
// =========================
// يحفظ زاوية الرقبة الأخيرة بحيث لما تطفي Sandy ثم تشغّلها، ترجع لنفس الزاوية.

#include <Preferences.h>

static Preferences g_prefs;
static const char* PREF_NS  = "sandy";
static const char* PREF_KEY_NECK = "neck";

void persistInit() {
  // RW namespace — read+write
  g_prefs.begin(PREF_NS, false);
}

int persistLoadNeckAngle(int fallback) {
  int v = g_prefs.getInt(PREF_KEY_NECK, fallback);
  if (v < SERVO_SAFE_MIN_ANGLE) v = SERVO_SAFE_MIN_ANGLE;
  if (v > SERVO_SAFE_MAX_ANGLE) v = SERVO_SAFE_MAX_ANGLE;
  return v;
}

void persistSaveNeckAngle(int angle) {
  // اكتب فقط إذا تغيرت القيمة لتقليل dump على flash
  static int lastSaved = -1;
  if (angle == lastSaved) return;
  g_prefs.putInt(PREF_KEY_NECK, angle);
  lastSaved = angle;
}
