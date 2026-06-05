// =========================
// Sandy — Base Motors (L298N)
// =========================
// كل دالة حركة بتسجّل آخر وقت أمر — والـ loop() يفحص الـ watchdog كل دورة.
// لو ما اجا أمر جديد خلال MOTOR_WATCHDOG_MS → auto-stop (safety).

static bool          g_motorsActive       = false;
static unsigned long g_lastMotorCommandMs = 0;

static void touchMotorWatchdog() {
  g_motorsActive       = true;
  g_lastMotorCommandMs = millis();
}

void stopMotors() {
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, LOW);
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, LOW);
  g_motorsActive = false;
  g_log.println("[MOTOR] Stop");
}

void moveForward() {
  digitalWrite(MOTOR_LEFT_IN1, HIGH);
  digitalWrite(MOTOR_LEFT_IN2, LOW);
  digitalWrite(MOTOR_RIGHT_IN3, HIGH);
  digitalWrite(MOTOR_RIGHT_IN4, LOW);
  touchMotorWatchdog();
  g_log.println("[MOTOR] Forward");
}

void moveBackward() {
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, HIGH);
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, HIGH);
  touchMotorWatchdog();
  g_log.println("[MOTOR] Backward");
}

void turnLeft() {
  digitalWrite(MOTOR_LEFT_IN1, LOW);
  digitalWrite(MOTOR_LEFT_IN2, HIGH); // العجلة اليسرى للخلف
  digitalWrite(MOTOR_RIGHT_IN3, HIGH);
  digitalWrite(MOTOR_RIGHT_IN4, LOW); // العجلة اليمنى للأمام
  touchMotorWatchdog();
  g_log.println("[MOTOR] Turn Left");
}

void turnRight() {
  digitalWrite(MOTOR_LEFT_IN1, HIGH);
  digitalWrite(MOTOR_LEFT_IN2, LOW);  // العجلة اليسرى للأمام
  digitalWrite(MOTOR_RIGHT_IN3, LOW);
  digitalWrite(MOTOR_RIGHT_IN4, HIGH); // العجلة اليمنى للخلف
  touchMotorWatchdog();
  g_log.println("[MOTOR] Turn Right");
}

// يُستدعى من loop() — يقتل الحركة لو ما اجا أمر جديد منذ MOTOR_WATCHDOG_MS
void checkMotorWatchdog() {
  if (!g_motorsActive) return;
  if (millis() - g_lastMotorCommandMs > MOTOR_WATCHDOG_MS) {
    g_log.println("[MOTOR] watchdog timeout — auto stop");
    stopMotors();
  }
}

void onBaseActionChange() {
  statusText = "base:" + baseAction;
  g_log.print("[CMD] Base Action: ");
  g_log.println(baseAction);

  if      (baseAction == "forward")  moveForward();
  else if (baseAction == "backward") moveBackward();
  else if (baseAction == "left")     turnLeft();
  else if (baseAction == "right")    turnRight();
  else if (baseAction == "stop")     stopMotors();
}
