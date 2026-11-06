#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"
#include "sdkconfig.h"

#include "app_state.h"
#include "collectors/ble_collector.h"
#include "collectors/lan_collector.h"
#include "collectors/pc_bridge.h"
#include "console/serial_console.h"
#include "mem_util.h"
#include "net/http_server.h"
#include "net/wifi_sta.h"
#include "sc_snapshot.h"
#include "storage/config_store.h"
#include "ui/ui.h"

static const char *TAG = "sc_main";

/* Heap (PSRAM preferred), not .bss: the snapshot is ~60 KB and would not fit in
 * the internal DRAM segment next to the WiFi/BLE stacks. */
static sc_snapshot_t *s_snapshot;
static char *s_json;
static size_t s_json_capacity;

static void network_changed(bool connected, const char *ip, int rssi, void *user)
{
    (void)user;
    app_state_set_network(connected, ip, rssi, "statuscast");
    ESP_LOGI(TAG, "network %s ip=%s rssi=%d", connected ? "up" : "down", ip != NULL ? ip : "-",
             rssi);
}

/* Config errors become schema-3 error rows, exactly like the Python dispatcher:
 * one bad entry never hides the other devices. */
static void append_config_errors(sc_snapshot_t *snapshot, int64_t now)
{
    int count = 0;
    const sc_config_error_t *errors = app_config_errors(&count);
    for (int i = 0; i < count; ++i) {
        sc_device_t *device = sc_snapshot_append(snapshot);
        if (device == NULL) {
            break;
        }
        sc_str_set_raw(&device->id, errors[i].id.has ? errors[i].id.v : "device");
        sc_str_set_raw(&device->platform, errors[i].platform.has ? errors[i].platform.v : "console");
        sc_str_set_raw(&device->media_state, "unknown");
        sc_str_set(&device->error, errors[i].message.has ? errors[i].message.v : "配置无效");
        device->updated_at.has = true;
        device->updated_at.v = now;
        device->has_transport = true;
        sc_str_set_raw(&device->transport.active, "none");
        device->transport.degraded.has = true;
        device->transport.degraded.v = false;
        device->transport.online.has = true;
        device->transport.online.v = false;
    }
}

static void run_round(void)
{
    const TickType_t start = xTaskGetTickCount();

    app_config_reload();
    const sc_config_t *cfg = app_config();
    const int64_t now = app_now_unix();

    sc_snapshot_init(s_snapshot);
    s_snapshot->generated_at = now;

    (void)lan_collector_run(cfg, s_snapshot, now);
    (void)ble_collector_run(cfg, s_snapshot, now);
    (void)pc_bridge_run(cfg, s_snapshot, now);
    append_config_errors(s_snapshot, now);

    /* Every row carries updatedAt == generatedAt, like the daemon. */
    for (int i = 0; i < s_snapshot->count; ++i) {
        if (!s_snapshot->devices[i].updated_at.has) {
            s_snapshot->devices[i].updated_at.has = true;
            s_snapshot->devices[i].updated_at.v = now;
        }
    }

    size_t length = sc_snapshot_render(s_snapshot, s_json, s_json_capacity);
    if (length == 0) {
        ESP_LOGE(TAG, "snapshot too large for the %u byte buffer", (unsigned)s_json_capacity);
        return;
    }
    if (!app_state_publish(s_json, length, s_snapshot)) {
        ESP_LOGE(TAG, "publish failed");
        return;
    }

    const uint32_t elapsed = (uint32_t)((xTaskGetTickCount() - start) * portTICK_PERIOD_MS);
    app_state_note_round(s_snapshot->count, elapsed);
    ESP_LOGI(TAG, "round: %d device(s), %u bytes, %u ms", s_snapshot->count, (unsigned)length,
             (unsigned)elapsed);
}

static void scheduler_task(void *arg)
{
    (void)arg;
    for (;;) {
        run_round();
        int seconds = app_config()->refresh_seconds;
        if (seconds < 5 || seconds > 3600) {
            seconds = 30;
        }
        vTaskDelay(pdMS_TO_TICKS((uint32_t)seconds * 1000u));
    }
}

void app_main(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);

    s_json_capacity = (size_t)CONFIG_SC_MAX_SNAPSHOT_JSON;
    s_snapshot = (sc_snapshot_t *)sc_psram_alloc(sizeof(sc_snapshot_t));
    s_json = (char *)sc_psram_alloc(s_json_capacity);
    if (s_snapshot == NULL || s_json == NULL) {
        ESP_LOGE(TAG, "out of memory for the snapshot buffers (%u + %u bytes)",
                 (unsigned)sizeof(sc_snapshot_t), (unsigned)s_json_capacity);
        esp_restart();
    }

    app_state_init();
    if (config_store_init() == ESP_OK) {
        (void)app_config_reload();
    } else {
        ESP_LOGW(TAG, "config store unavailable; defaults only (no persistence)");
    }

    /* The network stack must be up before the HTTP server binds a socket,
     * even when no credentials are provisioned yet. */
    (void)wifi_init();

    char ssid[64] = {0};
    char password[64] = {0};
    if (wifi_creds_load(ssid, sizeof(ssid), password, sizeof(password))) {
        (void)wifi_start(ssid, password, network_changed, NULL);
    } else {
        ESP_LOGW(TAG, "no WiFi credentials in NVS; use `net ssid <name>` + `net pass <pw>`");
    }

    (void)http_server_start((uint16_t)CONFIG_SC_HTTP_PORT);
    ble_collector_init();
    serial_console_start();
    ui_start();

    xTaskCreatePinnedToCore(scheduler_task, "sc_sched", 8192, NULL, 5, NULL, 1);
}
