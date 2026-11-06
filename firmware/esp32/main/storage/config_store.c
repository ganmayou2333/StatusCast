#include "config_store.h"

#include <stdio.h>
#include <string.h>

#include "esp_littlefs.h"
#include "esp_log.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char *TAG = "sc_store";

static bool s_mounted;

const char SC_DEFAULT_CONFIG_JSON[] =
    "{"
    "\"RefreshIntervalSeconds\":30,"
    "\"Aliases\":{},"
    "\"Consoles\":[],"
    "\"BleDevices\":[],"
    "\"PcFeed\":{\"enabled\":false,\"url\":\"http://192.168.1.10:5099/status/\","
    "\"timeoutSeconds\":5}"
    "}";

esp_err_t config_store_init(void)
{
    const esp_vfs_littlefs_conf_t conf = {
        .base_path = "/littlefs",
        .partition_label = "storage",
        .format_if_mount_failed = true,
        .dont_mount = false,
    };
    esp_err_t err = esp_vfs_littlefs_register(&conf);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "LittleFS mount failed: %s", esp_err_to_name(err));
        return err;
    }
    s_mounted = true;

    size_t total = 0;
    size_t used = 0;
    if (esp_littlefs_info("storage", &total, &used) == ESP_OK) {
        ESP_LOGI(TAG, "LittleFS: %u/%u bytes used", (unsigned)used, (unsigned)total);
    }

    /* Seed the default config the first time the board boots. */
    FILE *probe = fopen(CONFIG_STORE_PATH, "rb");
    if (probe == NULL) {
        ESP_LOGW(TAG, "config.json missing, writing compile-time default");
        (void)config_store_reset();
    } else {
        fclose(probe);
    }
    return ESP_OK;
}

bool config_store_available(void)
{
    return s_mounted;
}

bool config_store_read(char *dst, size_t cap, size_t *out_len)
{
    if (dst == NULL || cap == 0) {
        return false;
    }
    dst[0] = '\0';
    FILE *handle = fopen(CONFIG_STORE_PATH, "rb");
    if (handle == NULL) {
        return false;
    }
    size_t read = fread(dst, 1, cap - 1, handle);
    fclose(handle);
    if (read >= cap - 1) {
        /* Suspiciously large file: refuse rather than truncate into invalid JSON. */
        dst[0] = '\0';
        return false;
    }
    dst[read] = '\0';
    if (out_len != NULL) {
        *out_len = read;
    }
    return true;
}

bool config_store_write(const char *json, size_t len)
{
    if (!s_mounted || json == NULL) {
        return false;
    }
    const char *tmp_path = CONFIG_STORE_PATH ".tmp";
    FILE *handle = fopen(tmp_path, "wb");
    if (handle == NULL) {
        ESP_LOGE(TAG, "cannot open %s for writing", tmp_path);
        return false;
    }
    size_t written = fwrite(json, 1, len, handle);
    bool ok = (written == len) && (fflush(handle) == 0);
    fclose(handle);
    if (!ok) {
        ESP_LOGE(TAG, "short write to %s", tmp_path);
        remove(tmp_path);
        return false;
    }
    remove(CONFIG_STORE_PATH);
    if (rename(tmp_path, CONFIG_STORE_PATH) != 0) {
        ESP_LOGE(TAG, "rename failed, config not updated");
        remove(tmp_path);
        return false;
    }
    return true;
}

bool config_store_reset(void)
{
    return config_store_write(SC_DEFAULT_CONFIG_JSON, strlen(SC_DEFAULT_CONFIG_JSON));
}

/* ---------------------------------------------------------------- NVS ---- */

static bool nvs_open_state(nvs_open_mode_t mode, nvs_handle_t *handle)
{
    esp_err_t err = nvs_open("sc_state", mode, handle);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "nvs_open(sc_state) failed: %s", esp_err_to_name(err));
        return false;
    }
    return true;
}

bool state_load_console(int index, sc_persist_state_t *out)
{
    if (out == NULL) {
        return false;
    }
    memset(out, 0, sizeof(*out));
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READONLY, &handle)) {
        return false;
    }

    char key[16];
    (void)snprintf(key, sizeof(key), "c%d_fails", index);
    int32_t failures = 0;
    if (nvs_get_i32(handle, key, &failures) == ESP_OK) {
        out->failures = failures;
    }
    (void)snprintf(key, sizeof(key), "c%d_online", index);
    uint8_t online = 0;
    if (nvs_get_u8(handle, key, &online) == ESP_OK) {
        out->online = online != 0;
    }
    (void)snprintf(key, sizeof(key), "c%d_seen", index);
    int64_t seen = 0;
    if (nvs_get_i64(handle, key, &seen) == ESP_OK) {
        out->has_last_seen = true;
        out->last_seen_at = seen;
    }
    (void)snprintf(key, sizeof(key), "c%d_mac", index);
    size_t mac_len = sizeof(out->mac);
    if (nvs_get_str(handle, key, out->mac, &mac_len) == ESP_OK && out->mac[0] != '\0') {
        out->has_mac = true;
    }
    nvs_close(handle);
    return true;
}

bool state_save_console(int index, const sc_persist_state_t *state)
{
    if (state == NULL) {
        return false;
    }
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READWRITE, &handle)) {
        return false;
    }
    char key[16];
    (void)snprintf(key, sizeof(key), "c%d_fails", index);
    (void)nvs_set_i32(handle, key, state->failures);
    (void)snprintf(key, sizeof(key), "c%d_online", index);
    (void)nvs_set_u8(handle, key, state->online ? 1 : 0);
    if (state->has_last_seen) {
        (void)snprintf(key, sizeof(key), "c%d_seen", index);
        (void)nvs_set_i64(handle, key, state->last_seen_at);
        /* Also mirror into the generic slot so the console can show a summary. */
        (void)snprintf(key, sizeof(key), "c%d_online", index);
    }
    if (state->has_mac && state->mac[0] != '\0') {
        (void)snprintf(key, sizeof(key), "c%d_mac", index);
        (void)nvs_set_str(handle, key, state->mac);
    }
    esp_err_t err = nvs_commit(handle);
    nvs_close(handle);
    return err == ESP_OK;
}

bool state_load_ble_last_seen(int index, int64_t *out)
{
    if (out == NULL) {
        return false;
    }
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READONLY, &handle)) {
        return false;
    }
    char key[16];
    (void)snprintf(key, sizeof(key), "b%d_seen", index);
    int64_t value = 0;
    esp_err_t err = nvs_get_i64(handle, key, &value);
    nvs_close(handle);
    if (err == ESP_OK) {
        *out = value;
        return true;
    }
    return false;
}

bool state_save_ble_last_seen(int index, int64_t value)
{
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READWRITE, &handle)) {
        return false;
    }
    char key[16];
    (void)snprintf(key, sizeof(key), "b%d_seen", index);
    (void)nvs_set_i64(handle, key, value);
    esp_err_t err = nvs_commit(handle);
    nvs_close(handle);
    return err == ESP_OK;
}

bool wifi_creds_load(char *ssid, size_t ssid_cap, char *password, size_t password_cap)
{
    if (ssid == NULL || password == NULL) {
        return false;
    }
    ssid[0] = '\0';
    password[0] = '\0';
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READONLY, &handle)) {
        return false;
    }
    size_t ssid_len = ssid_cap;
    size_t pass_len = password_cap;
    esp_err_t ssid_err = nvs_get_str(handle, "wifi_ssid", ssid, &ssid_len);
    esp_err_t pass_err = nvs_get_str(handle, "wifi_pass", password, &pass_len);
    nvs_close(handle);
    return ssid_err == ESP_OK && pass_err == ESP_OK && ssid[0] != '\0';
}

bool wifi_creds_save(const char *ssid, const char *password)
{
    if (ssid == NULL || password == NULL) {
        return false;
    }
    nvs_handle_t handle;
    if (!nvs_open_state(NVS_READWRITE, &handle)) {
        return false;
    }
    (void)nvs_set_str(handle, "wifi_ssid", ssid);
    (void)nvs_set_str(handle, "wifi_pass", password);
    esp_err_t err = nvs_commit(handle);
    nvs_close(handle);
    return err == ESP_OK;
}
