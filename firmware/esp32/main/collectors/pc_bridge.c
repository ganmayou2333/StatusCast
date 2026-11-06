#include "pc_bridge.h"

#include <stdio.h>
#include <string.h>

#include "esp_http_client.h"
#include "esp_log.h"

#include "mem_util.h"
#include "net/wifi_sta.h"
#include "sc_wire_parse.h"

static const char *TAG = "sc_pc";

#define SC_PC_BODY_MAX 8192

/* PSRAM-backed on first use: 8 KB body + 16 device rows (~60 KB). */
static char *s_body;
static sc_device_t *s_last_rows;
static int s_last_count;

static bool ensure_buffers(void)
{
    if (s_body == NULL) {
        s_body = (char *)sc_psram_alloc(SC_PC_BODY_MAX);
    }
    if (s_last_rows == NULL) {
        s_last_rows = (sc_device_t *)sc_psram_alloc(sizeof(sc_device_t) * SC_MAX_DEVICES);
    }
    return s_body != NULL && s_last_rows != NULL;
}

void pc_bridge_reset(void)
{
    s_last_count = 0;
    s_body[0] = '\0';
}

static bool fetch(const sc_config_t *cfg, size_t *out_len)
{
    *out_len = 0;
    if (!cfg->pc_enabled || !cfg->pc_url.has) {
        return false;
    }
    if (!ensure_buffers()) {
        ESP_LOGE(TAG, "no memory for the PC bridge buffers");
        return false;
    }
    if (!wifi_is_connected()) {
        return false;
    }

    esp_http_client_config_t config = {
        .url = cfg->pc_url.v,
        .method = HTTP_METHOD_GET,
        .timeout_ms = cfg->pc_timeout_seconds * 1000,
        .disable_auto_redirect = false,
        .buffer_size = 1024,
        .buffer_size_tx = 512,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == NULL) {
        return false;
    }
    (void)esp_http_client_set_header(client, "Accept", "application/json");

    bool ok = false;
    size_t total = 0;
    esp_err_t err = esp_http_client_open(client, 0);
    if (err == ESP_OK) {
        int content_length = esp_http_client_fetch_headers(client);
        int status = esp_http_client_get_status_code(client);
        if (status == 200) {
            for (;;) {
                if (total >= SC_PC_BODY_MAX - 1) {
                    ESP_LOGW(TAG, "PC snapshot larger than %d bytes, truncated", SC_PC_BODY_MAX);
                    break;
                }
                int read = esp_http_client_read(client, s_body + total,
                                                SC_PC_BODY_MAX - 1 - (int)total);
                if (read < 0) {
                    ESP_LOGW(TAG, "read failed while fetching PC snapshot");
                    total = 0;
                    break;
                }
                if (read == 0) {
                    ok = true;
                    break;
                }
                total += (size_t)read;
            }
            if (content_length > 0 && total != (size_t)content_length && ok) {
                ESP_LOGW(TAG, "PC snapshot length mismatch: %d vs %u", content_length,
                         (unsigned)total);
            }
        } else {
            ESP_LOGW(TAG, "PC daemon answered HTTP %d", status);
        }
        (void)esp_http_client_close(client);
    } else {
        ESP_LOGW(TAG, "cannot reach PC daemon at %s: %s", cfg->pc_url.v,
                 esp_err_to_name(err));
    }
    esp_http_client_cleanup(client);

    if (ok && total >= 2) {
        s_body[total] = '\0';
        *out_len = total;
        return true;
    }
    return false;
}

int pc_bridge_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now)
{
    if (cfg == NULL || snapshot == NULL || !cfg->pc_enabled) {
        return 0;
    }
    if (!ensure_buffers()) {
        return 0;
    }

    size_t length = 0;
    if (fetch(cfg, &length)) {
        int parsed = sc_wire_parse_devices(s_body, length, &cfg->aliases, s_last_rows,
                                          SC_MAX_DEVICES);
        if (parsed >= 0) {
            s_last_count = parsed;
            int appended = sc_snapshot_merge(snapshot, s_last_rows, s_last_count);
            ESP_LOGI(TAG, "merged %d row(s) from PC", appended);
            return appended;
        }
        ESP_LOGW(TAG, "PC snapshot was not a schema-3 body, keeping last good rows");
    }

    if (s_last_count <= 0) {
        return 0;
    }

    /* Stale path: identical to StatusCoordinator.ToStaleDevice. */
    int appended = 0;
    for (int i = 0; i < s_last_count; ++i) {
        sc_device_t row = s_last_rows[i];
        sc_device_mark_stale(&row, SC_STALE_ERROR_TEXT);
        row.updated_at.has = true;
        row.updated_at.v = now;
        if (snapshot->count >= SC_MAX_DEVICES) {
            break;
        }
        snapshot->devices[snapshot->count] = row;
        snapshot->count += 1;
        appended += 1;
    }
    ESP_LOGW(TAG, "PC unreachable, reusing %d stale row(s)", appended);
    return appended;
}
