/*
 * config_store -- LittleFS-backed config.json plus the few NVS-backed runtime
 * facts (WiFi credentials and per-console presence hysteresis).
 *
 * Design rules from the spec:
 *   - config.json is intent, it lives on LittleFS and can be replaced over the
 *     serial console; the compile-time default is only a seed.
 *   - WiFi credentials are secrets: NVS only, never LittleFS, never logged,
 *     never echoed back by `cfg show`.
 *   - hysteresis state must survive a reboot, so it is written to NVS on every
 *     change (the daemon had the same requirement through console_state.json).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define CONFIG_STORE_PATH "/littlefs/config.json"
#define CONFIG_STORE_MAX_BYTES 8192

extern const char SC_DEFAULT_CONFIG_JSON[];

esp_err_t config_store_init(void);
bool config_store_available(void);

/* Read the whole file; returns false when it is absent or too large. */
bool config_store_read(char *dst, size_t cap, size_t *out_len);
bool config_store_write(const char *json, size_t len);
/* Overwrite the file with the compile-time default and return it. */
bool config_store_reset(void);

typedef struct {
    int failures;
    bool online;
    bool has_last_seen;
    int64_t last_seen_at;
    char mac[24];
    bool has_mac;
} sc_persist_state_t;

/* `index` is the position in sc_config_t.consoles; keys are index-based because
 * NVS limits key names to 15 characters. */
bool state_load_console(int index, sc_persist_state_t *out);
bool state_save_console(int index, const sc_persist_state_t *state);

bool state_load_ble_last_seen(int index, int64_t *out);
bool state_save_ble_last_seen(int index, int64_t value);

bool wifi_creds_load(char *ssid, size_t ssid_cap, char *password, size_t password_cap);
bool wifi_creds_save(const char *ssid, const char *password);
