#include "sandy_nvs.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_log.h"
#include "config.h"

static const char *TAG      = "nvs";
static const char *NVS_NS   = "sandy";
static const char *KEY_SERVO = "servo_pos";

esp_err_t nvs_sandy_init(void) {
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "partition truncated — erasing");
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

esp_err_t nvs_load_servo_angle(uint8_t *out_angle) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READONLY, &h);
    if (err != ESP_OK) return err;
    err = nvs_get_u8(h, KEY_SERVO, out_angle);
    nvs_close(h);
    return err;
}

esp_err_t nvs_save_servo_angle(uint8_t angle) {
    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READWRITE, &h);
    if (err != ESP_OK) return err;
    uint8_t cur = 0xFF;
    // Only write if changed — avoids unnecessary flash wear
    if (nvs_get_u8(h, KEY_SERVO, &cur) == ESP_OK && cur == angle) {
        nvs_close(h);
        return ESP_OK;
    }
    err = nvs_set_u8(h, KEY_SERVO, angle);
    if (err == ESP_OK) err = nvs_commit(h);
    nvs_close(h);
    return err;
}
