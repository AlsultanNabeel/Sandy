#ifndef SANDY_ESP32CAM_CONFIG_H
#define SANDY_ESP32CAM_CONFIG_H

// ===== AI Thinker ESP32-CAM pins =====
#ifndef PWDN_GPIO_NUM
  #define PWDN_GPIO_NUM 32
#endif
#ifndef RESET_GPIO_NUM
  #define RESET_GPIO_NUM -1
#endif
#ifndef XCLK_GPIO_NUM
  #define XCLK_GPIO_NUM 0
#endif
#ifndef SIOD_GPIO_NUM
  #define SIOD_GPIO_NUM 26
#endif
#ifndef SIOC_GPIO_NUM
  #define SIOC_GPIO_NUM 27
#endif
#ifndef Y9_GPIO_NUM
  #define Y9_GPIO_NUM 35
#endif
#ifndef Y8_GPIO_NUM
  #define Y8_GPIO_NUM 34
#endif
#ifndef Y7_GPIO_NUM
  #define Y7_GPIO_NUM 39
#endif
#ifndef Y6_GPIO_NUM
  #define Y6_GPIO_NUM 36
#endif
#ifndef Y5_GPIO_NUM
  #define Y5_GPIO_NUM 21
#endif
#ifndef Y4_GPIO_NUM
  #define Y4_GPIO_NUM 19
#endif
#ifndef Y3_GPIO_NUM
  #define Y3_GPIO_NUM 18
#endif
#ifndef Y2_GPIO_NUM
  #define Y2_GPIO_NUM 5
#endif
#ifndef VSYNC_GPIO_NUM
  #define VSYNC_GPIO_NUM 25
#endif
#ifndef HREF_GPIO_NUM
  #define HREF_GPIO_NUM 23
#endif
#ifndef PCLK_GPIO_NUM
  #define PCLK_GPIO_NUM 22
#endif

#define CAMERA_SERIAL_BAUD 115200
#define CAMERA_BOOT_DELAY_MS 500
#define CAMERA_WIFI_POLL_DELAY_MS 500
#define CAMERA_XCLK_FREQ_HZ 20000000

#define CAMERA_DEFAULT_FRAME_SIZE FRAMESIZE_VGA   // 640x480 — توازن جودة/حجم لـ Vision API
#define CAMERA_DEFAULT_JPEG_QUALITY 12             // 10-15 جيد، أقل = أعلى جودة + حجم أكبر
#define CAMERA_DEFAULT_FB_COUNT 1
#define CAMERA_VERTICAL_FLIP 1

// MQTT — نفس HiveMQ تبع Sandy
#define MQTT_RECONNECT_INTERVAL_MS  5000
#define STATUS_POST_INTERVAL_MS     10000           // كل 10s حالة
#define WIFI_RECONNECT_INTERVAL_MS  10000

// إرسال الصور: chunked publish — حجم صغير لتجنّب stack overflow في TLS write
#define SNAPSHOT_CHUNK_RAW_BYTES    1024            // 1KB raw → ~1.4KB base64
#define MQTT_BUFFER_SIZE            2048            // يكفي لـ chunk + JSON overhead
#define SNAPSHOT_INTER_CHUNK_DELAY_MS 5             // فاصل خفيف بين الـ chunks

// OTA
#define SANDY_OTA_HOSTNAME "sandy-esp32cam"

#endif
