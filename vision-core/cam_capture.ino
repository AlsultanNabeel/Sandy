// =========================
// ESP32-CAM — Camera Init + Snapshot Capture + Chunked Publish
// =========================
// التقاط: VGA 640x480 JPEG quality=12 → عادة 40-80KB
// النشر: تقسيم على chunks ~6KB base64 لكل واحد + JSON wrapper

#include "mbedtls/base64.h"

// ── إعلانات من cam_mqtt.ino ──
bool mqttPublishChunk(const char* payload, unsigned int len);
void mqttPublishEvent(const char* json);

static camera_config_t buildCameraConfig() {
  camera_config_t cfg = {};
  cfg.ledc_channel = LEDC_CHANNEL_0;
  cfg.ledc_timer = LEDC_TIMER_0;
  cfg.pin_d0 = Y2_GPIO_NUM;
  cfg.pin_d1 = Y3_GPIO_NUM;
  cfg.pin_d2 = Y4_GPIO_NUM;
  cfg.pin_d3 = Y5_GPIO_NUM;
  cfg.pin_d4 = Y6_GPIO_NUM;
  cfg.pin_d5 = Y7_GPIO_NUM;
  cfg.pin_d6 = Y8_GPIO_NUM;
  cfg.pin_d7 = Y9_GPIO_NUM;
  cfg.pin_xclk = XCLK_GPIO_NUM;
  cfg.pin_pclk = PCLK_GPIO_NUM;
  cfg.pin_vsync = VSYNC_GPIO_NUM;
  cfg.pin_href = HREF_GPIO_NUM;
  cfg.pin_sccb_sda = SIOD_GPIO_NUM;
  cfg.pin_sccb_scl = SIOC_GPIO_NUM;
  cfg.pin_pwdn = PWDN_GPIO_NUM;
  cfg.pin_reset = RESET_GPIO_NUM;
  cfg.xclk_freq_hz = CAMERA_XCLK_FREQ_HZ;
  cfg.pixel_format = PIXFORMAT_JPEG;
  cfg.frame_size = CAMERA_DEFAULT_FRAME_SIZE;
  cfg.jpeg_quality = CAMERA_DEFAULT_JPEG_QUALITY;
  cfg.fb_count = CAMERA_DEFAULT_FB_COUNT;
  cfg.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  cfg.fb_location = psramFound() ? CAMERA_FB_IN_PSRAM : CAMERA_FB_IN_DRAM;
  return cfg;
}

void setupCamera() {
  pinMode(PWDN_GPIO_NUM, OUTPUT);

  // دورة باور كاملة: مطفي ثم مشغّل — يساعد إذا الكام في حالة معلّقة
  digitalWrite(PWDN_GPIO_NUM, HIGH);  // power down
  delay(100);
  digitalWrite(PWDN_GPIO_NUM, LOW);   // power up
  delay(200);

  camera_config_t cfg = buildCameraConfig();

  // محاولة أولى
  esp_err_t err = esp_camera_init(&cfg);
  if (err != ESP_OK) {
    g_log.printf("[CAM] init attempt 1 failed: 0x%x — retrying...\n", err);
    esp_camera_deinit();
    digitalWrite(PWDN_GPIO_NUM, HIGH);
    delay(300);
    digitalWrite(PWDN_GPIO_NUM, LOW);
    delay(300);
    err = esp_camera_init(&cfg);
  }

  if (err != ESP_OK) {
    g_log.printf("[CAM] init FAILED: 0x%x — check ribbon + power 5V/1A+\n", err);
    g_cameraReady = false;
    return;
  }

  sensor_t* s = esp_camera_sensor_get();
  if (s) s->set_vflip(s, CAMERA_VERTICAL_FLIP);

  g_cameraReady = true;
  g_log.println("[CAM] ✅ camera ready");
}

void captureAndPublishSnapshot(const String& id) {
  if (!g_cameraReady) {
    // لا نحاول re-init هنا — esp_camera_init() ممكن يعلّق إذا الهاردوير مش راد
    // (ribbon غير مثبت، باور ناقص). نرسل خطأ فوراً بدل ما نهنّق الـ loop.
    g_log.println("[CAM] not ready — sending error without re-init");
    char err[100];
    snprintf(err, sizeof(err),
             "{\"id\":\"%s\",\"error\":\"camera_init_failed_at_boot\"}", id.c_str());
    mqttPublishEvent(err);
    return;
  }

  // ارمي إطاراً قديماً من الـ buffer (مهم: أول fb_get بعد فترة بيرجع إطار قديم)
  camera_fb_t* stale = esp_camera_fb_get();
  if (stale) esp_camera_fb_return(stale);

  // التقاط الإطار الحديث
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    g_log.println("[CAM] capture failed");
    char err[80];
    snprintf(err, sizeof(err), "{\"id\":\"%s\",\"error\":\"capture_failed\"}", id.c_str());
    mqttPublishEvent(err);
    return;
  }

  g_log.printf("[CAM] captured %u bytes — publishing...\n", fb->len);

  // قسّم على chunks
  unsigned int total = (fb->len + SNAPSHOT_CHUNK_RAW_BYTES - 1) / SNAPSHOT_CHUNK_RAW_BYTES;
  unsigned int seq = 0;
  size_t offset = 0;

  // مؤقت لـ base64: 1024 raw → ~1368 base64
  char b64buf[1500];
  char msgbuf[MQTT_BUFFER_SIZE];

  while (offset < fb->len) {
    size_t rawN = min((size_t)SNAPSHOT_CHUNK_RAW_BYTES, (size_t)(fb->len - offset));
    size_t b64Len = 0;
    int rc = mbedtls_base64_encode((unsigned char*)b64buf, sizeof(b64buf), &b64Len,
                                   fb->buf + offset, rawN);
    if (rc != 0) {
      g_log.printf("[CAM] base64 error rc=%d\n", rc);
      break;
    }
    b64buf[b64Len] = '\0';

    int n = snprintf(msgbuf, sizeof(msgbuf),
                     "{\"id\":\"%s\",\"seq\":%u,\"total\":%u,\"data\":\"%s\"}",
                     id.c_str(), seq, total, b64buf);
    if (n <= 0 || n >= (int)sizeof(msgbuf)) {
      g_log.println("[CAM] msg too big");
      break;
    }

    if (!mqttPublishChunk(msgbuf, n)) {
      g_log.printf("[CAM] publish failed at seq=%u\n", seq);
      break;
    }

    seq++;
    offset += rawN;
    delay(SNAPSHOT_INTER_CHUNK_DELAY_MS);
  }

  esp_camera_fb_return(fb);

  // علم انتهاء
  char done[120];
  snprintf(done, sizeof(done), "{\"id\":\"%s\",\"event\":\"complete\",\"chunks\":%u}",
           id.c_str(), seq);
  mqttPublishEvent(done);
  g_log.printf("[CAM] snapshot complete — %u chunks\n", seq);
}
