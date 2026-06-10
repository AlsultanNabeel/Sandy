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
#include "freertos/semphr.h"

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_crt_bundle.h"
#include "esp_netif_sntp.h"
#include "driver/i2s_std.h"
#include "mbedtls/md.h"
#include "esp_heap_caps.h"

#if ENABLE_WAKEWORD
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "model_path.h"
#endif

#include "sandy_wifi.h"
#if ENABLE_BUZZER
#include "sandy_buzzer.h"
#endif
#if ENABLE_FACE
#include "sandy_face.h"
#endif

static const char *TAG = "voice";

static esp_websocket_client_handle_t s_client;
static SemaphoreHandle_t s_ws_mutex;  // guards s_client create/send/destroy
static i2s_chan_handle_t s_rx_chan;   // INMP441 mic
static i2s_chan_handle_t s_tx_chan;   // MAX98357 amp
static StreamBufferHandle_t s_spk_stream;   // server audio waiting to play
static volatile bool s_authed;
static volatile int64_t s_last_rx_audio_ms;  // last time we got Sandy's audio
static volatile bool s_playing;              // true only while actively playing audio

// Playback health counters (cumulative since boot; reported when playback
// stops). dropped > 0 means the jitter buffer overflowed; gap restarts mean
// audible mid-reply dropouts — both point at delivery, not at the I2S side.
static volatile uint32_t s_spk_rx_bytes;     // audio received from the cloud
static volatile uint32_t s_spk_drop_bytes;   // received but didn't fit the buffer
static uint32_t s_spk_play_bytes;            // actually written to the amp
static int s_spk_gaps;                       // playback restarts within 2s

#if ENABLE_WAKEWORD
// Session is OPEN only between a wake word and the following silence. The WS
// (and so the paid Gemini link) is connected only while it's open.
static volatile bool s_session_active;       // WS up + mic streaming
static volatile bool s_wake_req;             // wake heard; manager should open
static volatile int64_t s_session_voice_ms;  // last user/Sandy activity while open

static const esp_wn_iface_t *s_wn;
static model_iface_data_t *s_wn_data;
static int s_wn_chunk;                        // samples per detect() call
static int16_t *s_wn_buf;                     // accumulates mic to chunk size
static int s_wn_fill;                         // samples currently in s_wn_buf
#else
static const bool s_session_active = true;    // no gate: always streaming
#endif

// ~100 ms frames at 16 kHz keep WebSocket overhead low without adding latency.
#define MIC_FRAME_SAMPLES   1600
#define SPK_CHUNK_BYTES     1920    // ~40 ms at 24 kHz / 16-bit
// Jitter buffer: hold this much before starting playback so uneven WiFi
// delivery doesn't underrun the I2S and make Sandy's voice stutter.
// 24 kHz · 16-bit = 48000 B/s, so 14400 B ≈ 300 ms — a bigger cushion against
// WiFi delivery jitter keeps playback from underrunning (less stutter).
#define SPK_PREBUF_BYTES    14400
// Output volume: right-shift each sample. 2 = quarter, 1 = half, 0 = full.
#define SPK_VOL_SHIFT       2


static int64_t now_ms(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
}

// True once the wall clock is real (post-2023), i.e. SNTP has set it.
static bool clock_is_set(void) {
    return time(NULL) > 1700000000;  // ~2023-11
}

// Block until SNTP sets the clock — the hello timestamp must land inside the
// server's 30s replay window, so connecting with a 1970 clock just gets us
// rejected. Wait up to ~60s; if it never syncs we proceed anyway and rely on
// the websocket's auto-reconnect to retry once the clock catches up.
static void sync_clock(void) {
    esp_sntp_config_t cfg = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    if (esp_netif_sntp_init(&cfg) != ESP_OK) {
        ESP_LOGW(TAG, "sntp init failed");
        return;
    }
    for (int i = 0; i < 60 && !clock_is_set(); i++) {
        esp_netif_sntp_sync_wait(pdMS_TO_TICKS(1000));
    }
    if (clock_is_set()) {
        ESP_LOGI(TAG, "clock synced");
    } else {
        ESP_LOGW(TAG, "clock not synced after wait; hello may be rejected");
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
    // Mic: two INMP441 on I2S_NUM_0, RX only, 32-bit STEREO (one mic per slot).
    // We read both slots — same as the proven sound-direction path — then mix
    // them down to mono for the cloud. Mono mode here read the wrong/empty slot
    // and only picked up a constant noise floor.
    i2s_chan_config_t rx_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    ESP_ERROR_CHECK(i2s_new_channel(&rx_cfg, NULL, &s_rx_chan));
    i2s_std_config_t rx_std = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(VOICE_IN_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        I2S_SLOT_MODE_STEREO),
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
    // Underrun must play SILENCE. With auto-clear off (the default) the DMA
    // replays its last block over and over on every starved moment — that's a
    // machine-gun trill layered on Sandy's voice, not a clean dropout.
    tx_cfg.auto_clear_after_cb = true;
    // Bigger hardware cushion: 6 desc × 720 frames ≈ 180 ms at 24 kHz mono, so
    // a late task switch doesn't instantly starve the amp.
    tx_cfg.dma_frame_num = 720;
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
        // ev->client, not s_client: this runs on the WS task, and the session
        // manager may already be swapping s_client for the next session.
        esp_websocket_client_send_text(ev->client, hello, n, portMAX_DELAY);
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
                size_t sent = xStreamBufferSend(s_spk_stream, ev->data_ptr, ev->data_len, 0);
                s_spk_rx_bytes += ev->data_len;
                if (sent < (size_t)ev->data_len) s_spk_drop_bytes += ev->data_len - sent;
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
    bool playing = false;
    int64_t first_seen = 0;   // when data first appeared while idle
    int64_t last_stop = 0;    // when playback last went idle
    for (;;) {
        if (!playing) {
            size_t avail = xStreamBufferBytesAvailable(s_spk_stream);
            if (avail == 0) {
                first_seen = 0;
                s_playing = false;
                // ≥ 2 ticks. At FREERTOS_HZ=100 a delay under 10ms rounds to
                // ZERO ticks and this loop busy-spins — at priority 9 on core 1
                // that silently starves the mic task and kills the wake word
                // (IDLE1 watchdog is off in sdkconfig, so nothing ever warned).
                vTaskDelay(pdMS_TO_TICKS(20));
                continue;
            }
            if (first_seen == 0) first_seen = now_ms();
            // Start once we have a cushion — or after 250ms even if it's a short
            // reply, so a small chunk never gets stuck unplayed (which would
            // keep the mic muted forever via half-duplex).
            if (avail >= SPK_PREBUF_BYTES || (now_ms() - first_seen) > 250) {
                playing = true;
                s_playing = true;
                // Restarting right after a stop = an audible mid-reply gap.
                if (last_stop && (now_ms() - last_stop) < 2000) s_spk_gaps++;
            } else {
                vTaskDelay(pdMS_TO_TICKS(20));  // same zero-tick trap as above
                continue;
            }
        }
        // 300ms tolerance: brief mid-reply WiFi gaps don't re-arm the cushion.
        size_t n = xStreamBufferReceive(s_spk_stream, buf, sizeof(buf), pdMS_TO_TICKS(300));
        if (n) {
#if SPK_VOL_SHIFT
            int16_t *s = (int16_t *)buf;
            for (int i = 0; i < (int)(n / sizeof(int16_t)); i++) s[i] >>= SPK_VOL_SHIFT;
#endif
            size_t written = 0;
            i2s_channel_write(s_tx_chan, buf, n, &written, portMAX_DELAY);
            s_spk_play_bytes += n;
        } else {
            playing = false;
            s_playing = false;
            first_seen = 0;
            last_stop = now_ms();
            // One line per reply: the health of the whole delivery chain.
            // rx≈played & dropped=0 & gaps=0 is a clean run.
            ESP_LOGI(TAG, "playback report: rx=%u played=%u dropped=%u gaps=%d",
                     (unsigned)s_spk_rx_bytes, (unsigned)s_spk_play_bytes,
                     (unsigned)s_spk_drop_bytes, s_spk_gaps);
        }
    }
}

#if ENABLE_WAKEWORD
// Load the WakeNet model packed into the "model" flash partition. Returns false
// if no model is present (caller then falls back to an always-on session).
static bool wakeword_init(void) {
    srmodel_list_t *models = esp_srmodel_init("model");
    if (!models || models->num <= 0) {
        ESP_LOGW(TAG, "no models in 'model' partition");
        return false;
    }
    char *name = esp_srmodel_filter(models, ESP_WN_PREFIX, NULL);
    if (!name) {
        ESP_LOGW(TAG, "no wakenet model found");
        return false;
    }
    s_wn = esp_wn_handle_from_name(name);
    s_wn_data = s_wn->create(name, DET_MODE_90);
    s_wn_chunk = s_wn->get_samp_chunksize(s_wn_data);
    s_wn_buf = malloc(s_wn_chunk * sizeof(int16_t));
    s_wn_fill = 0;
    ESP_LOGI(TAG, "wakenet '%s' ready (word='%s', chunk=%d, rate=%d)",
             name, esp_wn_wakeword_from_name(name), s_wn_chunk,
             s_wn->get_samp_rate(s_wn_data));
    return s_wn_buf != NULL;
}

// Feed mono 16-bit PCM in arbitrary lengths; WakeNet needs exact-chunk feeds, so
// we buffer up to s_wn_chunk and detect on each full chunk. Returns true if the
// wake word fired in this call.
static bool wakeword_feed(const int16_t *pcm, int n) {
    if (!s_wn) return false;
    bool hit = false;
    int i = 0;
    while (i < n) {
        int take = s_wn_chunk - s_wn_fill;
        if (take > n - i) take = n - i;
        memcpy(s_wn_buf + s_wn_fill, pcm + i, take * sizeof(int16_t));
        s_wn_fill += take;
        i += take;
        if (s_wn_fill == s_wn_chunk) {
            if (s_wn->detect(s_wn_data, s_wn_buf) == WAKENET_DETECTED) hit = true;
            s_wn_fill = 0;
        }
    }
    return hit;
}
#endif  // ENABLE_WAKEWORD

// Ship one mic frame up the WS. Drops the frame instead of blocking when the
// session manager is mid-teardown — the mic loop must never stall, or the
// wake-word listener stalls with it.
static void mic_send(const int16_t *pcm, int frames) {
    if (xSemaphoreTake(s_ws_mutex, 0) != pdTRUE) return;
    if (s_client && s_authed) {
        esp_websocket_client_send_bin(s_client, (const char *)pcm,
                                      frames * sizeof(int16_t), pdMS_TO_TICKS(1000));
    }
    xSemaphoreGive(s_ws_mutex);
}

// Read the mic, convert to 16-bit PCM, and stream it up while Sandy is quiet.
static void mic_task(void *arg) {
    // Stereo read: 2 int32 slots per frame. pcm holds the mono mix.
    int32_t *raw = malloc(MIC_FRAME_SAMPLES * 2 * sizeof(int32_t));
    int16_t *pcm = malloc(MIC_FRAME_SAMPLES * sizeof(int16_t));
    if (!raw || !pcm) {
        ESP_LOGE(TAG, "mic buffers alloc failed");
        vTaskDelete(NULL);
        return;
    }

    // One-pole DC blocker (high-pass): removes the INMP441's constant offset so
    // VAD sees real silence between words. y[n] = x[n] - x[n-1] + R*y[n-1].
    int32_t dc_x1 = 0, dc_y1 = 0;
    int64_t last_diag = 0;
    bool first_frame = true;

    for (;;) {
        size_t bytes_read = 0;
        // Bounded wait (not portMAX_DELAY): if I2S ever wedges, the task stays
        // observable instead of vanishing into an infinite block.
        if (i2s_channel_read(s_rx_chan, raw, MIC_FRAME_SAMPLES * 2 * sizeof(int32_t),
                             &bytes_read, pdMS_TO_TICKS(1000)) != ESP_OK) {
            continue;
        }
        if (first_frame) {
            first_frame = false;
            // One-shot "the mic path is alive" marker: if this line is missing
            // from a boot log, I2S RX is delivering nothing — look at wiring or
            // channel config, not at the cloud.
            ESP_LOGI(TAG, "mic up (first frame, %u bytes)", (unsigned)bytes_read);
        }
        int frames = bytes_read / (2 * sizeof(int32_t));

        // INMP441 gives 24-bit data left-justified in a 32-bit slot. Mix the two
        // mics, shift down to 16-bit (+gain), then DC-block.
        int64_t sum_abs = 0;
        for (int i = 0; i < frames; i++) {
            int32_t l = raw[2 * i]     >> VOICE_MIC_GAIN_SHIFT;
            int32_t r = raw[2 * i + 1] >> VOICE_MIC_GAIN_SHIFT;
            int32_t x = (l + r) / 2;

            int32_t y = x - dc_x1 + (dc_y1 - (dc_y1 >> 6));  // R ≈ 0.984
            dc_x1 = x;
            dc_y1 = y;

            if (y > 32767) y = 32767;
            else if (y < -32768) y = -32768;
            pcm[i] = (int16_t)y;
            sum_abs += (y < 0) ? -y : y;
        }

        int avg = (int)(sum_abs / (frames ? frames : 1));

        // Half-duplex: mute the mic only while we're ACTUALLY playing Sandy's
        // audio (plus a short tail), not merely when the buffer is non-empty —
        // a stuck buffered chunk used to mute the mic forever.
        bool sandy_talking = s_playing ||
                             (now_ms() - s_last_rx_audio_ms) < VOICE_HALF_DUPLEX_TAIL_MS;

#if ENABLE_WAKEWORD
        if (!s_session_active) {
            // Idle: listen locally for the wake word, stream nothing up. The
            // session manager opens the WS when it sees s_wake_req.
            if (!sandy_talking && wakeword_feed(pcm, frames)) {
                ESP_LOGI(TAG, "wake word detected");
                s_wake_req = true;
                // Local "I heard you" cue — fires on detection, before any cloud
                // connection, so it confirms the wake word independently of the
                // network and the (flaky) remote log.
#if ENABLE_BUZZER
                buzzer_play(MELODY_CURIOUS);
#endif
#if ENABLE_FACE
                face_set_mood(MOOD_CURIOUS);
#endif
            }
        } else {
            // Open session: hold it alive on user speech or Sandy's own audio;
            // the manager closes it once this goes quiet for VOICE_SESSION_IDLE_MS.
            if (avg > VOICE_SESSION_VAD_LEVEL || sandy_talking) {
                s_session_voice_ms = now_ms();
            }
            if (s_authed && !sandy_talking) {
                mic_send(pcm, frames);
            }
        }
#else
        if (s_authed && !sandy_talking) {
            mic_send(pcm, frames);
        }
#endif

        int64_t t = now_ms();
        if (t - last_diag > 1500) {
            last_diag = t;
#if ENABLE_WAKEWORD
            ESP_LOGI(TAG, "diag mic=%d session=%d authed=%d talking=%d",
                     avg, (int)s_session_active, (int)s_authed, (int)sandy_talking);
#else
            ESP_LOGI(TAG, "diag mic=%d authed=%d playing=%d talking=%d",
                     avg, (int)s_authed, (int)s_playing, (int)sandy_talking);
#endif
        }
    }
}


// One WS client per session: stop()+start() on the same client proved
// unreliable (after the first session closed, the next start never reconnected
// and voice went silent until reboot), so every session gets a fresh init and
// ends with a full destroy. s_ws_mutex keeps mic_send() off a client that is
// being torn down.
static bool ws_open(void) {
    esp_websocket_client_config_t cfg = {
        .uri = SANDY_VOICE_WS_URI,
        .crt_bundle_attach = esp_crt_bundle_attach,
        .buffer_size = 8192,
        // Above LVGL and the housekeeping tasks (default 5), below the audio
        // pair (8/9): TLS decrypt keeps up and audio arrives smoothly instead
        // of in starved bursts.
        .task_prio = 7,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms = 10000,
    };
    xSemaphoreTake(s_ws_mutex, portMAX_DELAY);
    s_client = esp_websocket_client_init(&cfg);
    if (s_client) {
        esp_websocket_register_events(s_client, WEBSOCKET_EVENT_ANY, on_ws_event, NULL);
        if (esp_websocket_client_start(s_client) != ESP_OK) {
            esp_websocket_client_destroy(s_client);
            s_client = NULL;
        }
    }
    bool ok = s_client != NULL;
    xSemaphoreGive(s_ws_mutex);
    if (!ok) ESP_LOGE(TAG, "ws open failed");
    return ok;
}

static void ws_close(void) {
    s_authed = false;
    xSemaphoreTake(s_ws_mutex, portMAX_DELAY);
    if (s_client) {
        esp_websocket_client_stop(s_client);
        esp_websocket_client_destroy(s_client);
        s_client = NULL;
    }
    xSemaphoreGive(s_ws_mutex);
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

    // Big buffer in PSRAM so a fast burst of Sandy's reply isn't dropped.
    // 1 MB ≈ 21 s of 24 kHz/16-bit audio: Gemini streams a long reply faster
    // than realtime, and the old 192 KB (~4 s) overflowed on them — the
    // overflow drops chopped whole pieces out of her sentences.
    s_spk_stream = xStreamBufferCreateWithCaps(1024 * 1024, 1, MALLOC_CAP_SPIRAM);
    s_ws_mutex = xSemaphoreCreateMutex();

    // Pin the audio tasks to core 1 (WiFi/TLS runs on core 0) and give playback
    // the higher priority so Sandy's voice never gets starved → no stutter.
    // Stacks live in internal RAM — if it's exhausted these fail SILENTLY and
    // voice just never answers, so check and shout.
    if (xTaskCreatePinnedToCore(spk_task, "voice_spk", 4096, NULL, 9, NULL, 1) != pdPASS ||
        xTaskCreatePinnedToCore(mic_task, "voice_mic", 4096, NULL, 8, NULL, 1) != pdPASS) {
        ESP_LOGE(TAG, "audio task create FAILED (heap_int free=%u largest=%u)",
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    }

#if ENABLE_WAKEWORD
    if (!wakeword_init()) {
        // No model packed — fall back to an always-on session so voice still
        // works; just without the cost gate.
        ESP_LOGW(TAG, "wake word unavailable; voice stays always-on");
        s_session_active = true;
        ws_open();
        vTaskDelete(NULL);
        return;
    }

    // Session manager: the paid Gemini link is connected ONLY between a wake
    // word and the silence that follows it.
    for (;;) {
        if (!s_session_active) {
            if (s_wake_req) {
                s_wake_req = false;
                ESP_LOGI(TAG, "opening voice session");
                if (ws_open()) {
                    s_session_voice_ms = now_ms();
                    s_session_active = true;
                }
                // On failure (WiFi blip mid-open) we just wait for the next
                // wake word — nothing to clean up, ws_open destroyed it all.
            }
        } else if ((now_ms() - s_session_voice_ms) > VOICE_SESSION_IDLE_MS && !s_playing) {
            ESP_LOGI(TAG, "session idle, closing");
            s_session_active = false;
            ws_close();
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
#else
    ws_open();
    vTaskDelete(NULL);  // setup done; the audio tasks carry on
#endif
}

esp_err_t voice_init(void) {
    xTaskCreate(voice_task, "voice", 6144, NULL, 5, NULL);
    return ESP_OK;
}

bool voice_is_connected(void) {
    return s_authed;
}
