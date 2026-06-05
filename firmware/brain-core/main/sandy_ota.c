#include "sandy_ota.h"
#include "esp_https_ota.h"
#include "esp_ota_ops.h"
#include "esp_log.h"

static const char *TAG = "ota";

esp_err_t ota_init(void) {
    const esp_partition_t *p = esp_ota_get_running_partition();
    ESP_LOGI(TAG, "running: %s @ 0x%lx", p->label, p->address);
    return ESP_OK;
}

void ota_trigger(const char *url) {
    if (!url || url[0] == '\0') {
        ESP_LOGE(TAG, "empty URL");
        return;
    }
    ESP_LOGI(TAG, "starting OTA from %s", url);
    esp_http_client_config_t http = {
        .url                        = url,
        .skip_cert_common_name_check = true,   // ⛏️ add cert for production
    };
    esp_https_ota_config_t ota = { .http_config = &http };
    esp_err_t err = esp_https_ota(&ota);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "OTA success — restarting");
        esp_restart();
    } else {
        ESP_LOGE(TAG, "OTA failed: %s", esp_err_to_name(err));
    }
}
