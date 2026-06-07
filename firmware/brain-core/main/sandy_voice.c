// Real-time voice link: I2S mic/speaker <-> /voice WebSocket (Gemini Live).
//
// Protocol (matches cloud/app/api/voice_ws.py):
//   1. Connect (WSS) and send a hello frame:
//        {"type":"hello","device_id":"...","ts":<unix_ms>,"hmac":"<hex>"}
//        hmac = HMAC-SHA256(SANDY_WS_HMAC_KEY, device_id + str(ts))
//   2. Wait for {"type":"auth_ok"}.
//   3. Mic up: binary PCM, 16-bit LE, 16 kHz mono.
//      Sandy down: binary PCM, 16-bit LE, 24 kHz mono.
//      Control frames (text JSON): {"type":"end_turn"} / {"type":"error",...}.
//
// Half-duplex: we stop sending the mic while Sandy is talking, otherwise the
// speaker leaks back into the mic and she answers her own voice.
//
// Untested on hardware yet: verify the I2S pins against your wiring and tune
// VOICE_MIC_GAIN_SHIFT once the board is assembled.

#include "sandy_voice.h"
#include "config.h"
#include "secrets.h"

#include <stdlib.h>
#include <string.h>
#include <sys/time.h>
#include <time.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_crt_bundle.h"
#include "esp_netif_sntp.h"
#include "driver/i2s_std.h"
#include "mbedtls/md.h"

#include "sandy_wifi.h"

static const char *TAG = "voice";

static esp_websocket_client_handle_t s_client;
static i2s_chan_handle_t s_rx_chan;   // INMP441 mic
static i2s_chan_handle_t s_tx_chan;   // MAX98357 amp
static StreamBufferHandle_t s_spk_stream;   // server audio waiting to play
static volatile bool s_authed;
static volatile int64_t s_last_rx_audio_ms;  // last time we got Sandy's audio

// ~100 ms frames at 16 kHz keep WebSocket overhead low without adding latency.
#define MIC_FRAME_SAMPLES   1600
#define SPK_CHUNK_BYTES     1920   // ~40 ms at 24 kHz / 16-bit


static int64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

// Wait (briefly) for SNTP so the hello timestamp is within the server's 30s
// replay window. We still try the handshake even if it times out.
static void sync_clock(void) {
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    if (esp_netif_sntp_init(&cfg) != ESP_OK) {
        ESP_LOGW(TAG, "sntp init failed");
        return;
    }
    if (esp_netif_sntp_sync_wait(pdMS_TO_TICKS(10000)) != ESP_OK) {
        ESP_LOGW(TAG, "sntp not synced yet, hello ts may be rejected");
    }
}

// Build the HMAC handshake frame into `out`. Returns the string length.
static int build_hello(char *out, size_t out_len) {
    int64_t ts = now_ms();

    char signed_msg[96];
    int n = snprintf(signed_msg, sizeof(signed_msg), "%s%lld", SANDY_DEVICE_ID, ts);

    unsigned char mac[32];
    const mbedtls_md_info_t *md = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
    mbedtls_md_hmac(md,
                    (const unsigned char *)SANDY_WS_HMAC_KEY, strlen(SANDY_WS_HMAC_KEY),
                    (const unsigned char *)signed_msg, n,
                    mac);

    char hex[65];
    for (int i = 0; i < 32; i++) {
        snprintf(hex + i * 2, 3, "%02x", mac[i]);
    }

    return snprintf(out, out_len,
                    "{\"type\":\"hello\",\"device_id\":\"%s\",\"ts\":%lld,\"hmac\":\"%s\"}",
                    SANDY_DEVICE_ID, ts, hex);
}


static esp_err_t i2s_start(void) {
    // Mic: INMP441 on I2S_NUM_0, RX only, 32-bit slot (24-bit data, left-justified).
    i2s_chan_config_t rx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&rx_cfg, NULL, &s_rx_chan));
    i2s_std_config_t rx_std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(VOICE_IN_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_MIC_SCK,
            .ws   = PIN_I2S_MIC_WS,
            .dout = I2S_GPIO_UNUSED,
            .din  = PIN_I2S_MIC_SD,
            .invert_flags = {0},
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_rx_chan, &rx_std));
    ESP_ERROR_CHECK(i2s_channel_enable(s_rx_chan));

    // Speaker: MAX98357 on I2S_NUM_1, TX only, 16-bit at 24 kHz (Gemini output).
    i2s_chan_config_t tx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&tx_cfg, &s_tx_chan, NULL));
    i2s_std_config_t tx_std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(VOICE_OUT_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                        I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_I2S_SPK_BCLK,
            .ws   = PIN_I2S_SPK_LRC,
            .dout = PIN_I2S_SPK_DIN,
            .din  = I2S_GPIO_UNUSED,
            .invert_flags = {0},
        },
    };
    ESP_ERROR_CHECK(i2s_channel_init_std_mode(s_tx_chan, &tx_std));
    ESP_ERROR_CHECK(i2s_channel_enable(s_tx_chan));
    return ESP_OK;
}


// Plain substring check is enough for the small fixed control frames.
static bool text_has(const char *data, int len, const char *needle) {
    static char buf[128];
    int n = len < (int)sizeof(buf) - 1 ? len : (int)sizeof(buf) - 1;
    memcpy(buf, data, n);
    buf[n] = '\0';
    return strstr(buf, needle) != NULL;
}

static void on_ws_event(void *arg, esp_event_base_t base, int32_t id, void *event_data) {
    esp_websocket_event_data_t *ev = (esp_websocket_event_data_t *)event_data;
    switch (id) {
    case WEBSOCKET_EVENT_CONNECTED: {
        char hello[192];
        int n = build_hello(hello, sizeof(hello));
        esp_websocket_client_send_text(s_client, hello, n, portMAX_DELAY);
        ESP_LOGI(TAG, "connected, sent hello");
        break;
    }
    case WEBSOCKET_EVENT_DATA:
        if (ev->op_code == 0x1) {  // text control frame
            if (text_has(ev->data_ptr, ev->data_len, "auth_ok")) {
                s_authed = true;
                ESP_LOGI(TAG, "auth ok, streaming");
            } else if (text_has(ev->data_ptr, ev->data_len, "end_turn")) {
                ESP_LOGD(TAG, "end of Sandy's turn");
            } else if (text_has(ev->data_ptr, ev->data_len, "error")) {
                ESP_LOGW(TAG, "server error frame");
            }
        } else if (ev->op_code == 0x2 || ev->op_code == 0x0) {  // binary audio (+ continuation)
            if (ev->data_len > 0) {
                s_last_rx_audio_ms = now_ms();
                xStreamBufferSend(s_spk_stream, ev->data_ptr, ev->data_len, 0);
            }
        }
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
        s_authed = false;
        ESP_LOGW(TAG, "disconnected");
        break;
    default:
        break;
    }
}


// Drain server audio into the speaker. Runs whether or not we're authed; it just
// idles when there's nothing to play.
static void spk_task(void *arg) {
    uint8_t buf[SPK_CHUNK_BYTES];
    for (;;) {
        size_t n = xStreamBufferReceive(s_spk_stream, buf, sizeof(buf), pdMS_TO_TICKS(100));
        if (n) {
            size_t written = 0;
            i2s_channel_write(s_tx_chan, buf, n, &written, portMAX_DELAY);
        }
    }
}

// Read the mic, convert to 16-bit PCM, and stream it up while Sandy is quiet.
static void mic_task(void *arg) {
    int32_t *raw = malloc(MIC_FRAME_SAMPLES * sizeof(int32_t));
    int16_t *pcm = malloc(MIC_FRAME_SAMPLES * sizeof(int16_t));
    if (!raw || !pcm) {
        ESP_LOGE(TAG, "mic buffers alloc failed");
        vTaskDelete(NULL);
        return;
    }

    for (;;) {
        size_t bytes_read = 0;
        if (i2s_channel_read(s_rx_chan, raw, MIC_FRAME_SAMPLES * sizeof(int32_t),
                             &bytes_read, portMAX_DELAY) != ESP_OK) {
            continue;
        }
        int samples = bytes_read / sizeof(int32_t);

        // INMP441 gives 24-bit data left-justified in a 32-bit slot. The shift
        // both down-converts to 16-bit and adds gain; tune it on hardware.
        for (int i = 0; i < samples; i++) {
            int32_t v = raw[i] >> VOICE_MIC_GAIN_SHIFT;
            if (v > 32767) v = 32767;
            else if (v < -32768) v = -32768;
            pcm[i] = (int16_t)v;
        }

        // Half-duplex: skip the mic while Sandy's audio is playing or just ended.
        bool sandy_talking = !xStreamBufferIsEmpty(s_spk_stream) ||
                             (now_ms() - s_last_rx_audio_ms) < VOICE_HALF_DUPLEX_TAIL_MS;

        if (s_authed && !sandy_talking) {
            esp_websocket_client_send_bin(s_client, (const char *)pcm,
                                          samples * sizeof(int16_t), portMAX_DELAY);
        }
    }
}


static void voice_task(void *arg) {
    while (!wifi_sandy_is_connected()) {
        vTaskDelay(pdMS_TO_TICKS(500));
    }
    sync_clock();

    if (i2s_start() != ESP_OK) {
        ESP_LOGE(TAG, "I2S init failed, voice disabled");
        vTaskDelete(NULL);
        return;
    }

    s_spk_stream = xStreamBufferCreate(32 * 1024, 1);

    esp_websocket_client_config_t cfg = {
        .uri = SANDY_VOICE_WS_URI,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 4096,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms = 10000,
    };
    s_client = esp_websocket_client_init(&cfg);
    esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_ws_event, NULL);
    esp_websocket_client_start(s_client);

    xTaskCreate(spk_task, "voice_spk", 4096, NULL, 6, NULL);
    xTaskCreate(mic_task, "voice_mic", 4096, NULL, 6, NULL);

    vTaskDelete(NULL);  // setup done; the audio tasks carry on
}

esp_err_t voice_init(void) {
    xTaskCreate(voice_task, "voice", 6144, NULL, 5, NULL);
    return ESP_OK;
}

bool voice_is_connected(void) {
    return s_authed;
}
