#include "config_guard.h"

#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

#include "app_state.h"
#include "audit.h"
#include "config_store.h"
#include "mem_util.h"
#include "sc_core.h"

static const char *TAG = "sc_cfg_guard";

#define CONFIG_PREV_PATH "/littlefs/config.prev"
#define ROLLBACK_DEADLINE_US (90LL * 1000 * 1000)

static SemaphoreHandle_t s_lock;
static bool s_armed;
static bool s_published;
static int64_t s_armed_at_us;
static char *s_backup; /* PSRAM, holds config.prev while restoring */

static bool nvs_pending_get(void)
{
    nvs_handle_t handle;
    if (nvs_open("sc_state", NVS_READONLY, &handle) != ESP_OK) {
        return false;
    }
    uint8_t value = 0;
    esp_err_t err = nvs_get_u8(handle, "cfg_pending", &value);
    nvs_close(handle);
    return err == ESP_OK && value != 0;
}

static void nvs_pending_set(bool pending)
{
    nvs_handle_t handle;
    if (nvs_open("sc_state", NVS_READWRITE, &handle) != ESP_OK) {
        return;
    }
    (void)nvs_set_u8(handle, "cfg_pending", pending ? 1 : 0);
    (void)nvs_commit(handle);
    nvs_close(handle);
}

static bool copy_config_to_prev(void)
{
    static char *buffer;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(CONFIG_STORE_MAX_BYTES);
        if (buffer == NULL) {
            return false;
        }
    }
    size_t length = 0;
    if (!config_store_read(buffer, CONFIG_STORE_MAX_BYTES, &length)) {
        return false;
    }
    FILE *handle = fopen(CONFIG_PREV_PATH, "wb");
    if (handle == NULL) {
        return false;
    }
    size_t written = fwrite(buffer, 1, length, handle);
    fclose(handle);
    return written == length;
}

static bool restore_prev(void)
{
    if (s_backup == NULL) {
        s_backup = (char *)sc_psram_alloc(CONFIG_STORE_MAX_BYTES);
        if (s_backup == NULL) {
            return false;
        }
    }
    FILE *handle = fopen(CONFIG_PREV_PATH, "rb");
    if (handle == NULL) {
        return false;
    }
    size_t length = fread(s_backup, 1, CONFIG_STORE_MAX_BYTES - 1, handle);
    fclose(handle);
    if (length == 0) {
        return false;
    }
    s_backup[length] = '\0';
    if (!config_store_write(s_backup, length)) {
        ESP_LOGE(TAG, "cannot restore the previous config");
        return false;
    }
    (void)app_config_reload();
    return true;
}

void config_guard_init(void)
{
    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
    }
    if (nvs_pending_get()) {
        /* The previous boot never published a snapshot with the pushed config. */
        ESP_LOGW(TAG, "pending config detected at boot, rolling back");
        (void)restore_prev();
        nvs_pending_set(false);
        audit_record("config-rollback", "boot", NULL);
    }
    s_armed = false;
    s_published = false;
    s_armed_at_us = 0;
}

esp_err_t config_guard_apply(const char *json, size_t len)
{
    if (json == NULL || len == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_lock != NULL) {
        (void)xSemaphoreTake(s_lock, portMAX_DELAY);
    }
    if (!copy_config_to_prev()) {
        ESP_LOGW(TAG, "no previous config to back up");
    }
    esp_err_t err = config_store_write(json, len) ? ESP_OK : ESP_FAIL;
    if (err == ESP_OK) {
        (void)app_config_reload();
        nvs_pending_set(true);
        s_armed = true;
        s_published = false;
        s_armed_at_us = esp_timer_get_time();
    }
    if (s_lock != NULL) {
        (void)xSemaphoreGive(s_lock);
    }
    return err;
}

void config_guard_note_publish(void)
{
    if (!s_armed) {
        return;
    }
    s_published = true;
    s_armed = false;
    nvs_pending_set(false);
}

void config_guard_poll(void)
{
    if (!s_armed) {
        return;
    }
    const int64_t elapsed = esp_timer_get_time() - s_armed_at_us;
    if (s_published) {
        config_guard_note_publish();
        return;
    }
    if (elapsed < ROLLBACK_DEADLINE_US) {
        return;
    }

    ESP_LOGW(TAG, "pushed config never produced a snapshot, rolling back");
    if (restore_prev()) {
        audit_record("config-rollback", "timeout", NULL);
    } else {
        audit_record("config-rollback", "failed", NULL);
    }
    nvs_pending_set(false);
    s_armed = false;
}

bool config_guard_pending(void)
{
    return s_armed;
}
