// Cable-free dev: OTA upload over HTTP + remote serial log over TCP.
// Both are dev conveniences — keep behind ENABLE_REMOTE.

#include "config.h"
#if ENABLE_REMOTE

#include "sandy_remote.h"
#include <string.h>
#include <stdarg.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/stream_buffer.h"
#include "esp_log.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "lwip/sockets.h"

static const char *TAG = "remote";

#ifndef MIN
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#endif

// ─── Remote serial log (TCP, port 3333) ─────────────────────────────────────
#define LOG_PORT       3333
#define LOG_BUF_BYTES  8192

static StreamBufferHandle_t s_logbuf;
static vprintf_like_t       s_old_vprintf;
static volatile bool        s_log_connected = false;

// Tee every esp_log line: to UART (as before) and into a buffer the log task
// drains to the TCP client. Re-entrant-safe (just vsnprintf + stream buffer).
static int log_vprintf(const char *fmt, va_list ap) {
    va_list cp;
    va_copy(cp, ap);
    int r = s_old_vprintf ? s_old_vprintf(fmt, ap) : 0;
    if (s_logbuf && s_log_connected) {
        char line[200];
        int n = vsnprintf(line, sizeof(line), fmt, cp);
        if (n > 0) xStreamBufferSend(s_logbuf, line, MIN(n, (int)sizeof(line)), 0);
    }
    va_end(cp);
    return r;
}

static void log_server_task(void *arg) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in addr = {
        .sin_family      = AF_INET,
        .sin_port        = htons(LOG_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    bind(srv, (struct sockaddr *)&addr, sizeof(addr));
    listen(srv, 1);

    for (;;) {
        int c = accept(srv, NULL, NULL);
        if (c < 0) { vTaskDelay(pdMS_TO_TICKS(200)); continue; }
        xStreamBufferReset(s_logbuf);
        s_log_connected = true;
        ESP_LOGI(TAG, "log client connected");
        char buf[256];
        for (;;) {
            size_t n = xStreamBufferReceive(s_logbuf, buf, sizeof(buf), pdMS_TO_TICKS(500));
            if (n > 0 && send(c, buf, n, 0) < 0) break;
        }
        s_log_connected = false;
        close(c);
    }
}

// ─── OTA upload (HTTP) ──────────────────────────────────────────────────────
static esp_err_t root_get(httpd_req_t *req) {
    static const char *page =
        "<h3>Sandy — OTA</h3>"
        "<p>Flash from the terminal:</p>"
        "<pre>curl --data-binary @build/sandy-brain-s3.bin http://DEVICE_IP/update</pre>";
    httpd_resp_send(req, page, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

static esp_err_t update_post(httpd_req_t *req) {
    const esp_partition_t *part = esp_ota_get_next_update_partition(NULL);
    if (!part) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "no OTA partition");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "OTA -> %s (%d bytes)", part->label, req->content_len);

    esp_ota_handle_t h;
    if (esp_ota_begin(part, OTA_SIZE_UNKNOWN, &h) != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "ota_begin failed");
        return ESP_FAIL;
    }

    char buf[1460];
    int remaining = req->content_len;
    while (remaining > 0) {
        int r = httpd_req_recv(req, buf, MIN(remaining, (int)sizeof(buf)));
        if (r == HTTPD_SOCK_ERR_TIMEOUT) continue;
        if (r <= 0) { esp_ota_abort(h); httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv error"); return ESP_FAIL; }
        if (esp_ota_write(h, buf, r) != ESP_OK) { esp_ota_abort(h); httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "ota_write failed"); return ESP_FAIL; }
        remaining -= r;
    }

    if (esp_ota_end(h) != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "image invalid");
        return ESP_FAIL;
    }
    if (esp_ota_set_boot_partition(part) != ESP_OK) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "set_boot failed");
        return ESP_FAIL;
    }
    httpd_resp_sendstr(req, "OK — rebooting into new firmware\n");
    ESP_LOGI(TAG, "OTA done — rebooting");
    vTaskDelay(pdMS_TO_TICKS(400));
    esp_restart();
    return ESP_OK;
}

static void start_http(void) {
    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.lru_purge_enable = true;
    cfg.recv_wait_timeout = 20;
    httpd_handle_t srv = NULL;
    if (httpd_start(&srv, &cfg) != ESP_OK) { ESP_LOGE(TAG, "httpd start failed"); return; }
    httpd_uri_t root = { .uri = "/",       .method = HTTP_GET,  .handler = root_get };
    httpd_uri_t upd  = { .uri = "/update", .method = HTTP_POST, .handler = update_post };
    httpd_register_uri_handler(srv, &root);
    httpd_register_uri_handler(srv, &upd);
}

esp_err_t remote_init(void) {
    s_logbuf = xStreamBufferCreate(LOG_BUF_BYTES, 1);
    s_old_vprintf = esp_log_set_vprintf(log_vprintf);
    xTaskCreate(log_server_task, "logsrv", 4096, NULL, 4, NULL);
    start_http();
    ESP_LOGI(TAG, "remote ready — OTA: http://<ip>/update   logs: nc <ip> %d", LOG_PORT);
    return ESP_OK;
}

#endif // ENABLE_REMOTE
