
#ifndef SANDY_FACES_H
#define SANDY_FACES_H


// تنفيذ كل التعديلات الفنية المطلوبة لشخصية أنمي معبرة وحيوية.

// --- الألوان ---
#define FACE_BG_INNER      0x0000
#define FACE_WHITE         0xFFFF
#define FACE_SOFT          0xAD55
#define FACE_BLUSH         0xD1B2
#define FACE_LIP           0xF206
#define FACE_LIP_SOFT      0xB26A
#define FACE_MOUTH_NEUTRAL 0x4228
#define FACE_MOUTH_EDGE    0x738E
#define FACE_MOUTH_INNER   0x18C3
#define FACE_HEART         0xF800
#define FACE_TEAR          0x5DFF
#define FACE_IRIS          0x03FF
#define FACE_IRIS_2        0x87FF
#define FACE_PUPIL         0x0000
#define FACE_NOSE          0x5ACB  

// --- متغيرات الحالة العامة ---
static TFT_eSprite faceSprite = TFT_eSprite(&tft);
static bool faceSpriteReady = false;
static bool faceShellReady = false;
static bool faceRendererReady = false;
static Mood lastRenderedMood = MOOD_IDLE;
static unsigned long lastFacePushMs = 0;

// --- متغيرات خاصة بالحركة (Animation) ---
static int zzz_phase = 0;
static int tear_wobble = 0;
static int tear_fall_phase = 0;  // 0..99 — تقدم سقوط الدمعة

// --- نظام جسيمات القلوب (للبوسة) ---
#define MAX_KISS_HEARTS 6
struct KissHeart {
  bool   alive;
  float  x, y;        // الموضع الحالي
  float  vx, vy;      // السرعة
  uint8_t life;       // 0..100 — يتناقص؛ 0 = ميت
  uint8_t size;       // 4..7
};
static KissHeart g_hearts[MAX_KISS_HEARTS] = {};

static inline void spawnKissHearts() {
  for (int i = 0; i < MAX_KISS_HEARTS; i++) {
    g_hearts[i].alive = true;
    g_hearts[i].x = 110 + random(-15, 16);
    g_hearts[i].y = 180 + random(-5, 6);
    g_hearts[i].vx = (random(-100, 100)) / 100.0f;
    g_hearts[i].vy = -(0.8f + random(0, 80) / 100.0f);
    g_hearts[i].life = 100;
    g_hearts[i].size = 4 + random(0, 4);
  }
}

template <typename Canvas>
static inline void drawHeartShape(Canvas &c, int cx, int cy, int sz, uint16_t color) {
  c.fillCircle(cx - sz/2, cy, sz/2, color);
  c.fillCircle(cx + sz/2, cy, sz/2, color);
  c.fillTriangle(cx - sz, cy + 1, cx + sz, cy + 1, cx, cy + sz + 2, color);
}

template <typename Canvas>
static inline void updateAndDrawHearts(Canvas &c) {
  for (int i = 0; i < MAX_KISS_HEARTS; i++) {
    if (!g_hearts[i].alive) continue;
    g_hearts[i].x += g_hearts[i].vx;
    g_hearts[i].y += g_hearts[i].vy;
    g_hearts[i].vy += 0.02f;  // gravity ضعيفة جداً
    if (g_hearts[i].life >= 2) g_hearts[i].life -= 2; else g_hearts[i].life = 0;
    if (g_hearts[i].life == 0 || g_hearts[i].y < 10) {
      g_hearts[i].alive = false;
      continue;
    }
    drawHeartShape(c, (int)g_hearts[i].x, (int)g_hearts[i].y, g_hearts[i].size, FACE_HEART);
  }
}

// --- لون قزحية القزحية حسب المود (تمييز عاطفي خفيف) ---
static inline uint16_t moodIrisColor(Mood mood) {
  switch (mood) {
    case MOOD_HAPPY:
    case MOOD_BIG_HAPPY:
    case MOOD_EXCITED:
    case MOOD_CUTE:        return 0x07E0;  // أخضر ناعم
    case MOOD_LOVE:
    case MOOD_KISS:
    case MOOD_HEART_EYES:
    case MOOD_SHY:         return 0xF81F;  // وردي
    case MOOD_SAD:
    case MOOD_CRY:
    case MOOD_EMPATHETIC:
    case MOOD_SLEEPY:
    case MOOD_BORED:       return 0x4A5F;  // أزرق هادئ
    case MOOD_ANGRY:
    case MOOD_ALERT:       return 0xF800;  // أحمر
    case MOOD_THINK:
    case MOOD_CONFUSED:
    case MOOD_CURIOUS:     return 0xFD20;  // برتقالي/كهرماني
    default:               return FACE_IRIS; // الافتراضي (سماوي)
  }
}

// --- شعار البوت ---
template <typename Canvas>
static inline void drawBootLogo(Canvas &c, uint8_t progress) {
  fillInnerPanel(c);
  c.setTextColor(FACE_WHITE);
  c.setTextSize(4);
  c.drawString("SANDY", 50, 70);
  c.setTextSize(1);
  c.setTextColor(FACE_SOFT);
  c.drawString("booting...", 80, 130);
  // loading bar
  int bw = 160;
  int bx = (220 - bw) / 2;
  int by = 160;
  c.drawRoundRect(bx, by, bw, 12, 4, FACE_SOFT);
  int fillW = (bw - 4) * progress / 100;
  c.fillRoundRect(bx + 2, by + 2, fillW, 8, 3, FACE_WHITE);
}

// --- دوال مساعدة ---
static inline void ensureFaceSprite() {
  if (faceSpriteReady) return;
  faceSprite.setColorDepth(16);
  faceSprite.createSprite(220, 220);
  faceSprite.setSwapBytes(false);
  faceSpriteReady = true;
}

static inline void drawFaceShellStatic() {
  tft.fillScreen(FACE_BG_INNER);
  tft.drawRoundRect(0, 0, 240, 240, 36, 0x2124);
  faceShellReady = true;
}

template <typename Canvas>
static inline void fillInnerPanel(Canvas &c) {
  c.fillRoundRect(0, 0, 220, 220, 24, FACE_BG_INNER);
}

static inline void clearFace() {
  faceShellReady = false;
  drawFaceShellStatic();
  ensureFaceSprite();
  fillInnerPanel(faceSprite);
}

// --- دوال الرسم المحدثة ---

template <typename Canvas>
static inline void drawNoseSoft(Canvas &c, int cx = 110, int cy = 130) {
  c.drawLine(cx, cy - 12, cx - 3, cy + 3, FACE_NOSE);
  c.drawLine(cx, cy - 12, cx + 3, cy + 3, FACE_NOSE);
  c.drawLine(cx - 3, cy + 3, cx - 8, cy + 8, FACE_NOSE);
  c.drawLine(cx + 3, cy + 3, cx + 8, cy + 8, FACE_NOSE);
}

template <typename Canvas>
static inline void drawEyeCore(Canvas &c, int cx, int cy, int rx, int ry, int pupilX, int pupilY, bool blink, int openness, bool heart_small, bool heart_big, uint16_t irisColor = FACE_IRIS) {
  int ex = cx - rx;
  int ey = cy - ry;
  int ew = rx * 2;
  int eh = ry * 2;
  int rr = max(12, ry - 2);

  if (blink) {
    c.fillRoundRect(ex - 3, ey - 2, ew + 6, eh + 6, rr, FACE_BG_INNER);
    c.drawWideLine(ex + 8, cy, ex + ew - 8, cy, 3, FACE_WHITE, FACE_BG_INNER);
    return;
  }

  c.fillRoundRect(ex, ey, ew, eh, rr, FACE_WHITE);
  c.drawRoundRect(ex, ey, ew, eh, rr, FACE_SOFT);

  int irisR = max(10, rx - 6);
  int px = cx + pupilX;
  int py = cy + pupilY;
  c.fillCircle(px, py, irisR, irisColor);
  c.fillCircle(px, py + 1, irisR - 4, FACE_IRIS_2);
  c.fillCircle(px, py + 1, irisR / 2, FACE_PUPIL);
  c.fillCircle(px - irisR / 3, py - irisR / 3, max(2, irisR / 3), FACE_WHITE);

  if (heart_small) c.fillCircle(px, py, 4, FACE_HEART);
  if (heart_big) c.fillCircle(px, py, 7, FACE_HEART);

  if (openness < 100) {
    int coverH = (ry * 2 * (100 - openness)) / 100;
    c.fillRoundRect(ex - 2, ey - 2, ew + 4, coverH + 2, rr, FACE_BG_INNER);
  }
}

template <typename Canvas>
static inline void drawWinkEye(Canvas &c, int cx, int cy) {
    c.drawWideLine(cx - 20, cy - 3, cx + 20, cy - 3, 3, FACE_WHITE, FACE_BG_INNER);
    c.drawWideLine(cx - 20, cy + 3, cx + 20, cy + 3, 3, FACE_WHITE, FACE_BG_INNER);
    c.drawPixel(cx - 15, cy, FACE_WHITE);
    c.drawPixel(cx + 15, cy, FACE_WHITE);
}

template <typename Canvas>
static inline void drawFoldedArms(Canvas &c, int cx, int cy) {
    c.drawWideLine(cx - 30, cy, cx + 30, cy, 5, FACE_WHITE, FACE_BG_INNER);
    c.drawWideLine(cx - 25, cy + 8, cx + 25, cy + 8, 4, FACE_SOFT, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawMouthKiss(Canvas &c) {
  c.fillCircle(110, 180, 8, FACE_WHITE);
  c.fillCircle(110, 180, 4, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawMouthTalkFrame(Canvas &c, bool playful = false) {
  // هذا هو الكود الأصلي لرسم الفم أثناء الكلام
  int randomW = 40 + random(0, 25);
  int randomH = 18 + random(0, 12);

  switch (talkFrame % 4) {
    case 0: 
      c.fillEllipse(110, 185, randomW / 2, randomH / 2, FACE_WHITE);
      c.fillEllipse(110, 185, (randomW / 2) - 4, (randomH / 2) - 4, FACE_BG_INNER);
      break;
    case 1: 
      c.fillEllipse(110, 185, (randomW - 5) / 2, (randomH + 5) / 2, FACE_WHITE);
      c.fillEllipse(110, 185, ((randomW - 5) / 2) - 4, ((randomH + 5) / 2) - 4, FACE_BG_INNER);
      break;
    case 2: 
      c.fillEllipse(110, 185, (randomW + 5) / 2, (randomH - 3) / 2, FACE_WHITE);
      c.fillEllipse(110, 185, ((randomW + 5) / 2) - 4, ((randomH - 3) / 2) - 4, FACE_BG_INNER);
      break;
    default: 
      c.fillEllipse(110, 185, (randomW - 10) / 2, randomH / 2, FACE_WHITE);
      c.fillEllipse(110, 185, ((randomW - 10) / 2) - 4, (randomH / 2) - 4, FACE_BG_INNER);
      break;
  }
}

// Z's تطلع من تحت لفوق، ٣ موجات متعاقبة، أحجام تنقص للأعلى
template <typename Canvas>
static inline void drawZzzAnimation(Canvas &c, int phase) {
    c.setTextColor(FACE_SOFT);
    int baseX = 165;
    for (int i = 0; i < 3; i++) {
        int p = (phase + i * 33) % 100;
        int y = 180 - (p * 150 / 100);           // ١٨٠ → ٣٠
        int x = baseX + ((p / 8) % 6) - 3;       // تموج خفيف لليمين/الشمال
        int sz = 3 - (p / 40);                   // كبير تحت، صغير فوق
        if (sz < 1) sz = 1;
        c.setTextSize(sz);
        c.drawString(i == 0 ? "Z" : "z", x, y);
    }
}

template <typename Canvas>
static inline void drawTearAnimation(Canvas &c, int wobble) {
    c.fillCircle(45, 135 + wobble, 10, FACE_TEAR);
    c.fillCircle(175, 135 - wobble, 10, FACE_TEAR);
}

// دموع نازلة (للبكاء) — حركة سقوط متكررة
template <typename Canvas>
static inline void drawFallingTears(Canvas &c, int phase) {
    int y1 = 130 + (phase * 90) / 100;
    int phase2 = (phase + 50) % 100;
    int y2 = 130 + (phase2 * 90) / 100;
    if (y1 < 215) {
        c.fillCircle(45, y1, 9, FACE_TEAR);
        c.fillCircle(45, y1 - 5, 6, FACE_TEAR);  // ذيل قطرة
    }
    if (y2 < 215) {
        c.fillCircle(175, y2, 9, FACE_TEAR);
        c.fillCircle(175, y2 - 5, 6, FACE_TEAR);
    }
}

// --- حواجب ---
// drop=true → الطرف الداخلي تحت (غاضب)؛ drop=false → الطرف الداخلي فوق (حزين/قلق)
template <typename Canvas>
static inline void drawBrowAngled(Canvas &c, int cx, bool right, bool drop, int y, int len, int tilt, int thick) {
    int inner = right ? (cx - len/2) : (cx + len/2);
    int outer = right ? (cx + len/2) : (cx - len/2);
    int innerY = drop ? (y + tilt) : (y - tilt);
    int outerY = drop ? (y - tilt) : (y + tilt);
    c.drawWideLine(inner, innerY, outer, outerY, thick, FACE_WHITE, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawBrowFlat(Canvas &c, int cx, int y, int len, int thick) {
    c.drawWideLine(cx - len/2, y, cx + len/2, y, thick, FACE_WHITE, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawBrowArch(Canvas &c, int cx, int y, int len, int peak, int thick) {
    c.drawWideLine(cx - len/2, y + peak/2, cx, y - peak, thick, FACE_WHITE, FACE_BG_INNER);
    c.drawWideLine(cx, y - peak, cx + len/2, y + peak/2, thick, FACE_WHITE, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawBrows(Canvas &c, Mood mood) {
  const int leftCx = 52, rightCx = 168;
  const int browY = 38;
  const int len = 40;
  const int thick = 5;

  switch (mood) {
    case MOOD_ANGRY:
      drawBrowAngled(c, leftCx,  false, true, browY, len, 10, thick);
      drawBrowAngled(c, rightCx, true,  true, browY, len, 10, thick);
      break;
    case MOOD_SAD:
    case MOOD_CRY:
    case MOOD_EMPATHETIC:
      drawBrowAngled(c, leftCx,  false, false, browY, len, 9, thick);
      drawBrowAngled(c, rightCx, true,  false, browY, len, 9, thick);
      break;
    case MOOD_SURPRISED:
    case MOOD_ALERT:
      drawBrowArch(c, leftCx,  browY - 6, len, 6, thick);
      drawBrowArch(c, rightCx, browY - 6, len, 6, thick);
      break;
    case MOOD_CONFUSED:
    case MOOD_CURIOUS:
      drawBrowFlat(c, leftCx, browY, len, thick);
      drawBrowAngled(c, rightCx, true, false, browY, len, 7, thick);
      break;
    case MOOD_THINK:
      drawBrowFlat(c, leftCx, browY, len, thick);
      drawBrowFlat(c, rightCx, browY - 5, len, thick);
      break;
    case MOOD_HAPPY:
    case MOOD_BIG_HAPPY:
    case MOOD_EXCITED:
    case MOOD_LOVE:
    case MOOD_HEART_EYES:
    case MOOD_CUTE:
    case MOOD_KISS:
    case MOOD_WINK:
    case MOOD_SHY:
      drawBrowArch(c, leftCx,  browY, len, 4, thick);
      drawBrowArch(c, rightCx, browY, len, 4, thick);
      break;
    case MOOD_SLEEPY:
    case MOOD_BORED:
    case MOOD_YAWN:
      // عيون شبه مغلقة — تخطّى الحواجب
      break;
    default:
      drawBrowFlat(c, leftCx, browY, len, thick);
      drawBrowFlat(c, rightCx, browY, len, thick);
      break;
  }
}

// --- خدود زهرية ---
template <typename Canvas>
static inline void drawCheekBlush(Canvas &c) {
    c.fillEllipse(35, 150, 11, 6, FACE_BLUSH);
    c.fillEllipse(185, 150, 11, 6, FACE_BLUSH);
}

// --- فم منحني (قوس مفتوح للأعلى = ابتسامة، للأسفل = حزن) ---
template <typename Canvas>
static inline void drawSmileArc(Canvas &c, int cx, int cy, int w, int h) {
    int rx = w / 2;
    int ry = h / 2;
    c.fillEllipse(cx, cy, rx, ry, FACE_WHITE);
    c.fillEllipse(cx, cy, rx - 4, ry - 4, FACE_BG_INNER);
    c.fillRect(cx - rx - 2, cy - ry - 2, w + 4, ry + 2, FACE_BG_INNER);
}

template <typename Canvas>
static inline void drawFrownArc(Canvas &c, int cx, int cy, int w, int h) {
    int rx = w / 2;
    int ry = h / 2;
    c.fillEllipse(cx, cy, rx, ry, FACE_WHITE);
    c.fillEllipse(cx, cy, rx - 4, ry - 4, FACE_BG_INNER);
    c.fillRect(cx - rx - 2, cy, w + 4, ry + 4, FACE_BG_INNER);
}

// --- فم البوسة (قلب صغير) ---
template <typename Canvas>
static inline void drawMouthHeart(Canvas &c, int cx, int cy) {
    c.fillCircle(cx - 6, cy - 2, 6, FACE_LIP);
    c.fillCircle(cx + 6, cy - 2, 6, FACE_LIP);
    c.fillTriangle(cx - 10, cy, cx + 10, cy, cx, cy + 12, FACE_LIP);
}

// --- دالة الرسم الرئيسية المحدثة بالكامل ---
template <typename Canvas>
static inline void renderMood(Canvas &c, Mood mood) {
  bool blink = millis() < blinkUntilMs;
  fillInnerPanel(c);

  // القيم الافتراضية
  int openness = 100;
  int pupilY = 0;
  bool heart_s = false, heart_b = false;

  // تحديد حالة العيون
  switch(mood) {
    case MOOD_THINK:  openness = 80; break;
    case MOOD_BORED:  openness = 55; break;
    case MOOD_SLEEPY: openness = 50; break;              // نعس — نص عين
    case MOOD_YAWN:   openness = 15; break;              // تثاؤب — عيون مغمضة + فم
    case MOOD_SAD: case MOOD_EMPATHETIC: case MOOD_CRY: pupilY = 8; break;
    case MOOD_LOVE: heart_s = true; break;
    case MOOD_HEART_EYES: heart_b = true; break;
    default: break;
  }
  // التثاؤب والنوم — عيون مغمضة كاملاً
  if (mood == MOOD_YAWN || mood == MOOD_ASLEEP) blink = true;
  
  // رسم العيون — مع لون قزحية حسب المود
  uint16_t iris = moodIrisColor(mood);
  if (mood == MOOD_WINK) {
      drawEyeCore(c, 52, 90, 48, 40, eyeOffsetX, eyeOffsetY, false, 100, false, false, iris);
      drawWinkEye(c, 168, 90);
  } else {
      drawEyeCore(c, 52, 90, 48, 40, eyeOffsetX, pupilY, blink, openness, heart_s, heart_b, iris);
      drawEyeCore(c, 168, 90, 48, 40, eyeOffsetX, pupilY, blink, openness, heart_s, heart_b, iris);
  }

  // رسم الحواجب
  drawBrows(c, mood);

  // رسم الأنف
  drawNoseSoft(c);

  // رسم الفم والعناصر الإضافية
  switch(mood) {
    case MOOD_IDLE:
        c.drawWideLine(90, 180, 130, 180, 4, FACE_WHITE, FACE_BG_INNER);
        break;
    case MOOD_BIG_HAPPY:
        drawSmileArc(c, 110, 175, 80, 36);
        drawCheekBlush(c);
        break;
    case MOOD_SMIRK:
        c.drawWideLine(85, 184, 135, 174, 4, FACE_WHITE, FACE_BG_INNER);
        break;
    case MOOD_CUTE:
    case MOOD_HAPPY:
        drawSmileArc(c, 110, 175, 60, 24);
        drawCheekBlush(c);
        break;
    case MOOD_EXCITED:
        drawSmileArc(c, 110, 175, 70, 30);
        drawCheekBlush(c);
        break;
    case MOOD_SHY:
        drawSmileArc(c, 110, 178, 36, 14);
        drawCheekBlush(c);
        // خدود إضافية أعرض للخجل
        c.fillEllipse(35, 152, 14, 7, FACE_BLUSH);
        c.fillEllipse(185, 152, 14, 7, FACE_BLUSH);
        break;
    case MOOD_KISS:
        drawMouthHeart(c, 110, 178);
        drawCheekBlush(c);
        break;
    case MOOD_LOVE:
    case MOOD_HEART_EYES:
        drawSmileArc(c, 110, 178, 50, 20);
        drawCheekBlush(c);
        break;
    case MOOD_CALM:
        c.drawWideLine(90, 180, 130, 180, 4, FACE_WHITE, FACE_BG_INNER);
        drawFoldedArms(c, 110, 205);
        break;
    case MOOD_YAWN:
        // فم مفتوح بيضاوي كبير + لسان داخل + Zzz فوق العيون لتأكيد التثاؤب
        c.fillEllipse(110, 188, 42, 30, FACE_WHITE);
        c.fillEllipse(110, 188, 36, 24, FACE_MOUTH_INNER);
        c.fillEllipse(110, 195, 22, 10, FACE_LIP);  // لسان
        break;
    case MOOD_CONFUSED:
        c.drawWideLine(90, 182, 105, 178, 4, FACE_WHITE, FACE_BG_INNER);
        c.drawWideLine(105, 178, 120, 182, 4, FACE_WHITE, FACE_BG_INNER);
        break;
    case MOOD_ANGRY:
        // فم مضغوط مستقيم — التعبير من الحواجب
        c.drawWideLine(85, 184, 135, 184, 5, FACE_WHITE, FACE_BG_INNER);
        break;
    case MOOD_SURPRISED:
    case MOOD_ALERT:
        // فم دائري مفتوح
        c.fillCircle(110, 182, 13, FACE_WHITE);
        c.fillCircle(110, 182, 9, FACE_BG_INNER);
        break;
    case MOOD_EMPATHETIC:
        drawTearAnimation(c, tear_wobble);
        drawFrownArc(c, 110, 188, 56, 18);
        break;
    case MOOD_CRY:
        drawFallingTears(c, tear_fall_phase);
        drawFrownArc(c, 110, 188, 56, 18);
        break;
    case MOOD_SAD:
        drawFrownArc(c, 110, 188, 56, 18);
        break;
    case MOOD_TALK:
        drawMouthTalkFrame(c);
        break;
    default: // CURIOUS, THINK, إلخ
        c.drawWideLine(90, 180, 130, 180, 4, FACE_WHITE, FACE_BG_INNER);
        break;
  }

  // Zzz لـ YAWN (تثاؤب) و ASLEEP (نوم) — كلاهما يستخدم نفس الانيميشن من تحت لفوق
  if (mood == MOOD_YAWN || mood == MOOD_ASLEEP) drawZzzAnimation(c, zzz_phase);

  // قلوب طائرة — تطلع لمّا spawnKissHearts ينادى، وتختفي تدريجياً
  updateAndDrawHearts(c);
}

// --- دوال التحكم ---
static inline bool faceNeedsRedraw() {
  return (millis() - lastFacePushMs > 25);
}

static inline void markFaceRendered() {
  lastRenderedMood = currentMood;
  lastFacePushMs = millis();
}

static inline void drawFace() {
  ensureFaceSprite();
  if (!faceShellReady) drawFaceShellStatic();
  if (!faceNeedsRedraw()) return;
  renderMood(faceSprite, currentMood);
  faceSprite.pushSprite(10, 10);
  faceRendererReady = true;
  markFaceRendered();
}

#endif