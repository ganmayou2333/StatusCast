/*
 * sc_core -- IDF-independent data model and small pure-logic helpers shared by
 * the firmware and the host-side unit tests.
 *
 * Nothing in this header or sc_core.c may include an esp_* header: the host
 * test build compiles these files with a plain C compiler, and the firmware
 * build adds them to main/. That is what keeps the byte-level contract
 * (13 keys, additive blocks, alias resolution, routing, hysteresis) testable
 * without a board.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define SC_STR_LEN 96
#define SC_LIST_ITEM_LEN 32
#define SC_MAX_DEVICES 16
#define SC_MAX_ALIASES 24
#define SC_MAX_CONSOLES 8
#define SC_MAX_BLE_DEVICES 8
#define SC_MAX_TCP_PORTS 8
#define SC_MAX_LIST_ITEMS 6
/* Room for one re-printed nekotrak passthrough subtree (pet / pomodoro).
 * Sized from reality, not from a round number: the reference machine's pet is
 * already 309 bytes, so 448 left only ~138 bytes of headroom before the subtree
 * was dropped silently. Anything that still does not fit is reported through
 * nekotrak.warns rather than disappearing. */
#define SC_RAW_JSON_LEN 512
#define SC_MAX_CONFIG_ERRORS 12
/* console_collector semantics: two consecutive failures mark a host offline,
 * a single success brings it straight back online. */
#define SC_FAILS_TO_OFFLINE 2

/* ---------- optional scalars (a missing value must render as null) ---------- */

typedef struct {
    bool has;
    char v[SC_STR_LEN];
} sc_str_t;

typedef struct {
    bool has;
    int64_t v;
} sc_i64_t;

typedef struct {
    bool has;
    int32_t v;
} sc_i32_t;

typedef struct {
    bool has;
    bool v;
} sc_bool_t;

typedef struct {
    int count;
    char items[SC_MAX_LIST_ITEMS][SC_LIST_ITEM_LEN];
} sc_strlist_t;

/* ---------- platform tokens (load-bearing for NekoTool's render rules) ------ */

typedef enum {
    SC_PLATFORM_IOS,
    SC_PLATFORM_ANDROID,
    SC_PLATFORM_CONSOLE,
    SC_PLATFORM_MACOS,
    SC_PLATFORM_LINUX,
    SC_PLATFORM_BLE,
    SC_PLATFORM_WINDOWS,
    SC_PLATFORM_UNKNOWN,
} sc_platform_t;

const char *sc_platform_token(sc_platform_t platform);
sc_platform_t sc_platform_from_token(const char *token);

/* ---------- additive blocks (field order is the contract's order) ---------- */

typedef struct {
    sc_bool_t online;
    sc_str_t method;
    sc_i64_t last_seen_at;
    sc_str_t mac;
    sc_bool_t idle_hint;
    sc_str_t name;
    sc_str_t type;
    sc_str_t status_text;
} sc_console_block_t;

typedef struct {
    sc_bool_t ssh;
    sc_bool_t online;
    sc_str_t status_text;
    sc_str_t method;
    sc_str_t source;
    sc_str_t hostname;
    sc_i64_t last_seen_at;
    sc_i64_t uptime_seconds;
} sc_node_block_t;

typedef struct {
    sc_str_t active;
    sc_bool_t degraded;
    sc_bool_t online;
    sc_i64_t last_seen_at;
    sc_str_t detail;
} sc_transport_block_t;

typedef struct {
    sc_str_t address;
    sc_str_t name;
    sc_i32_t rssi;
    sc_strlist_t services;
    sc_bool_t connected;
    sc_i32_t heart_rate;
    sc_str_t status_text;
    sc_i64_t last_seen_at;
} sc_ble_block_t;

typedef struct {
    sc_str_t version;
    sc_str_t source;
    sc_i64_t uptime_sec;
    /* The daemon emits these four as ISO-8601 strings, not epoch seconds. */
    sc_str_t started_at;
    sc_str_t last_log_at;
    sc_bool_t tracker_active;
    sc_str_t tracker_started_at;
    sc_str_t tracker_stopped_at;
    sc_str_t current_game;
    sc_i32_t current_game_app_id;
    bool has_pet;
    char pet_json[SC_RAW_JSON_LEN];
    bool has_pomodoro;
    char pomodoro_json[SC_RAW_JSON_LEN];
    sc_strlist_t warns;
} sc_nekotrak_block_t;

/* ---------- a snapshot device row (13 keys + optional additive blocks) ----- */

typedef struct {
    sc_str_t id;
    sc_str_t platform;
    sc_str_t model;
    sc_str_t display_name;
    sc_i32_t battery_level;
    sc_bool_t is_charging;
    sc_str_t media_title;
    sc_str_t media_artist;
    sc_str_t media_state;
    sc_str_t foreground_app;
    sc_str_t foreground_app_name;
    sc_i64_t updated_at;
    sc_str_t error;

    bool has_console;
    sc_console_block_t console;
    bool has_node;
    sc_node_block_t node;
    bool has_transport;
    sc_transport_block_t transport;
    bool has_ble;
    sc_ble_block_t ble;
    bool has_nekotrak;
    sc_nekotrak_block_t nekotrak;
} sc_device_t;

typedef struct {
    int64_t generated_at;
    int count;
    sc_device_t devices[SC_MAX_DEVICES];
} sc_snapshot_t;

void sc_device_init(sc_device_t *device);
void sc_snapshot_init(sc_snapshot_t *snapshot);
sc_device_t *sc_snapshot_append(sc_snapshot_t *snapshot);
sc_device_t *sc_snapshot_find(sc_snapshot_t *snapshot, const char *id);

/* ---------- strings ---------- */

void sc_str_clear(sc_str_t *s);
/* Copy with .NET-style Trim(): leading/trailing whitespace dropped, an empty
 * result becomes "absent" so it renders as null. */
void sc_str_set(sc_str_t *s, const char *src);
/* Copy verbatim (no trim, no blank-to-null) -- for tokens we control. */
void sc_str_set_raw(sc_str_t *s, const char *src);
bool sc_str_is_blank(const char *s);
bool sc_str_eq(const char *a, const char *b);
bool sc_str_eq_ci_ascii(const char *a, const char *b);
void sc_strlist_clear(sc_strlist_t *list);
bool sc_strlist_add(sc_strlist_t *list, const char *item);

/* ---------- aliases / displayName ---------- */

typedef struct {
    char from[SC_STR_LEN];
    char to[SC_STR_LEN];
} sc_alias_t;

typedef struct {
    int count;
    sc_alias_t items[SC_MAX_ALIASES];
} sc_alias_table_t;

void sc_alias_table_init(sc_alias_table_t *table);
bool sc_alias_table_add(sc_alias_table_t *table, const char *from, const char *to);
/* daemon rule: alias -> hard-coded iPhone map -> raw model -> 未知机型. */
const char *sc_display_name(const sc_alias_table_t *table, const char *model, char *out, size_t out_cap);

/* ---------- routing ---------- */

typedef enum {
    SC_ROUTE_STATUS,
    SC_ROUTE_HEALTH,
    SC_ROUTE_NOT_FOUND,
    SC_ROUTE_METHOD_NOT_ALLOWED,
} sc_route_t;

/* Method is checked first: any non-GET (even for unknown paths) is 405.
 * Path match is exact and case-sensitive, query string already stripped. */
sc_route_t sc_route_resolve(const char *method, const char *path);

/* ---------- /health readiness ---------- */

typedef struct {
    bool ok;
    bool has_device_count;
    int32_t device_count;
    bool has_generated_at;
    int64_t generated_at;
    bool has_schema;
    int32_t schema;
} sc_health_t;

/* Mirrors StatusServer.cs: 200 only when the body parses as an object whose
 * `devices` member is an array; generatedAt/schema stay null when absent. */
void sc_health_evaluate_json(const char *json, size_t len, sc_health_t *out);

/* ---------- console presence hysteresis ---------- */

typedef struct {
    int failures;
    bool online;
    bool has_last_seen;
    int64_t last_seen_at;
} sc_probe_state_t;

/* Apply one probe result; returns the (possibly unchanged) online flag.
 * A success resets the failure counter and stamps last_seen_at. */
bool sc_hysteresis_apply(sc_probe_state_t *state, bool probe_ok, int64_t now);

/* ---------- round budget ---------- */

/* Wall-clock budget for one collection round, derived from the configured
 * period. Two seconds are held back so a round can never overlap the next one,
 * and the result is capped so a round always finishes well inside the 30 s task
 * watchdog. Both bounds are load-bearing: without the margin rounds overlap,
 * without the cap a long round trips the watchdog and reboots the board. */
int64_t sc_round_budget_ms(int refresh_seconds);

/* True once `now_ms` has reached `deadline_ms`. Monotonic milliseconds from any
 * consistent source. */
bool sc_deadline_reached(int64_t deadline_ms, int64_t now_ms);

/* ---------- config ---------- */

typedef struct {
    sc_str_t id;
    sc_str_t name;
    sc_str_t type;
    sc_str_t ip;
    sc_str_t offline_text;
    int tcp_port_count;
    uint16_t tcp_ports[SC_MAX_TCP_PORTS];
    bool fallback;
} sc_console_cfg_t;

typedef struct {
    sc_str_t id;
    sc_str_t name;
    sc_str_t address;
    sc_str_t name_filter;
    bool want_battery;
    bool want_heart_rate;
    int heart_rate_wait_seconds;
} sc_ble_cfg_t;

typedef struct {
    int refresh_seconds;
    sc_alias_table_t aliases;
    int console_count;
    sc_console_cfg_t consoles[SC_MAX_CONSOLES];
    int ble_count;
    sc_ble_cfg_t ble[SC_MAX_BLE_DEVICES];
    bool pc_enabled;
    sc_str_t pc_url;
    int pc_timeout_seconds;
} sc_config_t;

typedef struct {
    sc_str_t platform;
    sc_str_t id;
    sc_str_t message;
} sc_config_error_t;

void sc_config_defaults(sc_config_t *cfg);
/* Parse config.json (PC-compatible subset). Missing/broken JSON keeps all
 * defaults. Returns the number of entry-level errors written to `errors`;
 * those become schema-3 error rows, never a failed round. */
int sc_config_parse(const char *json, size_t len, sc_config_t *cfg,
                    sc_config_error_t *errors, int max_errors);

/* Defaults shared with the Python/C# side. */
#define SC_DEFAULT_REFRESH_SECONDS 30
#define SC_DEFAULT_HR_WAIT_SECONDS 3
#define SC_DEFAULT_PC_TIMEOUT_SECONDS 5
#define SC_DEFAULT_OFFLINE_TEXT "离线"
