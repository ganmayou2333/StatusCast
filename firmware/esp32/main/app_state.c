#include "app_state.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sdkconfig.h"

#include "config_store.h"
#include "mem_util.h"

static const char *TAG = "sc_state";

#ifndef CONFIG_SC_MAX_SNAPSHOT_JSON
#define CONFIG_SC_MAX_SNAPSHOT_JSON 8192
#endif

static SemaphoreHandle_t s_lock;
static sc_config_t s_config;
static sc_config_error_t s_config_errors[SC_MAX_CONFIG_ERRORS];
static int s_config_error_count;

static char *s_body;
static size_t s_body_capacity;
static size_t s_body_len;
static bool s_ready;
static int64_t s_generated_at;
static int s_device_count;
static sc_display_model_t s_display;

static app_network_t s_network;
static uint32_t s_last_round_ms;
static app_sensor_t s_sensor;
static uint8_t s_i2c_addresses[16];
static int s_i2c_count;

/* Wall clock: valid once SNTP has synced. Anything before 2023-11-14 is
 * treated as "not synced yet" so timestamps never look plausible-but-wrong. */
#define SC_TIME_EPOCH_FLOOR 1700000000LL

static void state_lock(void)
{
    if (s_lock != NULL) {
        (void)xSemaphoreTake(s_lock, portMAX_DELAY);
    }
}

static void state_unlock(void)
{
    if (s_lock != NULL) {
        (void)xSemaphoreGive(s_lock);
    }
}

void app_state_init(void)
{
    if (s_lock == NULL) {
        s_lock = xSemaphoreCreateMutex();
    }
    sc_config_defaults(&s_config);
    s_config_error_count = 0;
    if (s_body == NULL) {
        s_body_capacity = (size_t)CONFIG_SC_MAX_SNAPSHOT_JSON;
        s_body = (char *)sc_psram_alloc(s_body_capacity);
    }
    s_body_len = 0;
    s_ready = false;
    s_generated_at = 0;
    s_device_count = 0;
    memset(&s_display, 0, sizeof(s_display));
    memset(&s_network, 0, sizeof(s_network));
    (void)snprintf(s_network.hostname, sizeof(s_network.hostname), "%s", "statuscast");
    s_last_round_ms = 0;
}

/* ------------------------------------------------------------------ config */

bool app_config_reload(void)
{
    static char *buffer;
    static sc_config_t *parsed_config;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(CONFIG_STORE_MAX_BYTES);
        parsed_config = (sc_config_t *)sc_psram_alloc(sizeof(sc_config_t));
        if (buffer == NULL || parsed_config == NULL) {
            ESP_LOGE(TAG, "no memory for config parsing");
            return false;
        }
    }
    size_t length = 0;
    bool have_file = config_store_read(buffer, CONFIG_STORE_MAX_BYTES, &length);

    s_config_error_count = 0;
    if (have_file) {
        s_config_error_count = sc_config_parse(buffer, length, parsed_config, s_config_errors,
                                               SC_MAX_CONFIG_ERRORS);
    } else {
        sc_config_defaults(parsed_config);
    }

    state_lock();
    s_config = *parsed_config;
    state_unlock();

    for (int i = 0; i < s_config_error_count; ++i) {
        ESP_LOGW(TAG, "config error [%s/%s]: %s", s_config_errors[i].platform.v,
                 s_config_errors[i].id.v, s_config_errors[i].message.v);
    }
    return have_file;
}

const sc_config_t *app_config(void)
{
    return &s_config;
}

const sc_config_error_t *app_config_errors(int *count)
{
    if (count != NULL) {
        *count = s_config_error_count;
    }
    return s_config_errors;
}

bool app_config_save_json(const char *json, size_t len)
{
    if (json == NULL || len == 0) {
        return false;
    }
    if (!config_store_write(json, len)) {
        return false;
    }
    return app_config_reload();
}

/* ------------------------------------------------------------------- time */

int64_t app_now_unix(void)
{
    time_t now = time(NULL);
    if (now < (time_t)SC_TIME_EPOCH_FLOOR) {
        return 0;
    }
    return (int64_t)now;
}

bool app_time_valid(void)
{
    return app_now_unix() != 0;
}

/* -------------------------------------------------------------- publishing */

bool app_state_publish(const char *json, size_t len, const sc_snapshot_t *snapshot)
{
    if (json == NULL || snapshot == NULL || len == 0 || s_body == NULL ||
        len >= s_body_capacity) {
        return false;
    }
    state_lock();
    memcpy(s_body, json, len);
    s_body[len] = '\0';
    s_body_len = len;
    s_ready = true;
    s_generated_at = snapshot->generated_at;
    s_device_count = snapshot->count;
    sc_display_build(snapshot, &s_display);
    state_unlock();
    return true;
}

bool app_state_ready(void)
{
    return s_ready;
}

size_t app_state_copy(char *dst, size_t cap)
{
    if (dst == NULL || cap == 0) {
        return 0;
    }
    state_lock();
    size_t length = 0;
    if (s_ready && s_body_len > 0 && s_body_len < cap) {
        memcpy(dst, s_body, s_body_len);
        dst[s_body_len] = '\0';
        length = s_body_len;
    }
    state_unlock();
    return length;
}

int app_state_device_count(void)
{
    state_lock();
    int count = s_device_count;
    state_unlock();
    return count;
}

int64_t app_state_generated_at(void)
{
    state_lock();
    int64_t value = s_generated_at;
    state_unlock();
    return value;
}

/* ----------------------------------------------------------------- display */

bool app_state_copy_display(sc_display_model_t *out)
{
    if (out == NULL) {
        return false;
    }
    state_lock();
    *out = s_display;
    bool ready = s_ready;
    state_unlock();
    return ready;
}

/* ----------------------------------------------------------------- network */

void app_state_set_network(bool connected, const char *ip, int rssi, const char *hostname)
{
    state_lock();
    s_network.wifi_connected = connected;
    if (ip != NULL) {
        (void)snprintf(s_network.ip, sizeof(s_network.ip), "%s", ip);
    } else {
        s_network.ip[0] = '\0';
    }
    s_network.rssi = rssi;
    if (hostname != NULL && hostname[0] != '\0') {
        (void)snprintf(s_network.hostname, sizeof(s_network.hostname), "%s", hostname);
    }
    state_unlock();
}

app_network_t app_state_network(void)
{
    state_lock();
    app_network_t copy = s_network;
    state_unlock();
    return copy;
}

void app_state_set_sensor(bool ok, float temperature_c, float humidity_pct)
{
    state_lock();
    s_sensor.ok = ok;
    s_sensor.temperature_c = temperature_c;
    s_sensor.humidity_pct = humidity_pct;
    state_unlock();
}

app_sensor_t app_state_sensor(void)
{
    state_lock();
    app_sensor_t copy = s_sensor;
    state_unlock();
    return copy;
}

void app_state_set_i2c(const uint8_t *addresses, int count)
{
    state_lock();
    s_i2c_count = 0;
    for (int i = 0; i < count && i < (int)sizeof(s_i2c_addresses); ++i) {
        s_i2c_addresses[s_i2c_count++] = addresses[i];
    }
    state_unlock();
}

const char *app_state_i2c_list(char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return "";
    }
    out[0] = '\0';
    state_lock();
    size_t used = 0;
    for (int i = 0; i < s_i2c_count; ++i) {
        int written = snprintf(out + used, cap - used, "%s\"0x%02X\"",
                               i == 0 ? "" : ",", (unsigned)s_i2c_addresses[i]);
        if (written <= 0 || (size_t)written >= cap - used) {
            break;
        }
        used += (size_t)written;
    }
    state_unlock();
    return out;
}

void app_state_note_round(int devices, uint32_t millis)
{
    state_lock();
    s_last_round_ms = millis;
    (void)devices;
    state_unlock();
}

uint32_t app_state_last_round_millis(void)
{
    state_lock();
    uint32_t value = s_last_round_ms;
    state_unlock();
    return value;
}
