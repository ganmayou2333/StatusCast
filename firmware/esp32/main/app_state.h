/*
 * app_state -- the small amount of mutable state shared between the collector
 * task, the HTTP server, the console and the display.
 *
 * Only the published snapshot body and a render-ready display model live here;
 * collectors build their rows locally inside the scheduler task and publish
 * once per round. Everything is guarded by one mutex because every accessor is
 * called from a different task.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "sc_core.h"
#include "sc_display.h"

typedef struct {
    bool wifi_connected;
    char ip[16];
    int rssi;
    char hostname[32];
} app_network_t;

void app_state_init(void);

/* ---- config (reloaded from LittleFS before each round) ---- */
bool app_config_reload(void);
const sc_config_t *app_config(void);
/* Entry-level config errors found by the last reload; the scheduler turns each
 * one into a schema-3 error row. */
const sc_config_error_t *app_config_errors(int *count);
/* Replace /config.json contents; used by `cfg put` and `cfg reset`. */
bool app_config_save_json(const char *json, size_t len);

/* ---- wall clock ---- */
int64_t app_now_unix(void);
bool app_time_valid(void);

/* ---- published snapshot ---- */
bool app_state_publish(const char *json, size_t len, const sc_snapshot_t *snapshot);
bool app_state_ready(void);
/* Copy the published body; returns the length or 0 when not ready. */
size_t app_state_copy(char *dst, size_t cap);
int app_state_device_count(void);

/* ---- display model ---- */
bool app_state_copy_display(sc_display_model_t *out);
/* generatedAt of the published snapshot (0 when nothing is published yet). */
int64_t app_state_generated_at(void);

/* ---- network facts for the overview page ---- */
void app_state_set_network(bool connected, const char *ip, int rssi, const char *hostname);
app_network_t app_state_network(void);

/* ---- diagnostics: optional sensor + I2C inventory ---- */
typedef struct {
    bool ok;
    float temperature_c;
    float humidity_pct;
} app_sensor_t;

void app_state_set_sensor(bool ok, float temperature_c, float humidity_pct);
app_sensor_t app_state_sensor(void);
void app_state_set_i2c(const uint8_t *addresses, int count);
/* Comma separated "0x38,0x3C" list for /diag; empty when nothing answered. */
const char *app_state_i2c_list(char *out, size_t cap);

/* ---- counters for diagnostics ---- */
void app_state_note_round(int devices, uint32_t millis);
uint32_t app_state_last_round_millis(void);
