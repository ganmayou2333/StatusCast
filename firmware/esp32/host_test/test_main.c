/*
 * Host-side unit tests for the IDF-independent contract core.
 *
 * These run without a board and without ESP-IDF; see tools/host-test.ps1.
 * The last section cross-checks the rendered device key order against
 * schema/status-contract.json, the single source of truth shared with the
 * Python and C# implementations.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "sc_core.h"
#include "sc_json.h"
#include "sc_led.h"
#include "sc_limits.h"
#include "sc_route_counts.h"
#include "sc_snapshot.h"
#include "sc_wire_parse.h"

static int g_checks;
static int g_failures;

#define CHECK(cond)                                                          \
    do {                                                                     \
        g_checks += 1;                                                       \
        if (!(cond)) {                                                       \
            g_failures += 1;                                                 \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);           \
        }                                                                    \
    } while (0)

#define CHECK_STR(actual, expected)                                          \
    do {                                                                     \
        g_checks += 1;                                                       \
        const char *a_ = (actual);                                           \
        const char *e_ = (expected);                                         \
        if (a_ == NULL || strcmp(a_, e_) != 0) {                             \
            g_failures += 1;                                                 \
            printf("FAIL %s:%d: got [%s] want [%s]\n", __FILE__, __LINE__,   \
                   a_ ? a_ : "(null)", e_);                                  \
        }                                                                    \
    } while (0)

static void test_json_escape(void)
{
    char buf[128];

    CHECK(sc_json_escape("abc", buf, sizeof(buf)) == 3);
    CHECK_STR(buf, "abc");

    CHECK(sc_json_escape("+", buf, sizeof(buf)) == 6);
    CHECK_STR(buf, "\\u002B");

    CHECK(sc_json_escape("\"", buf, sizeof(buf)) == 2);
    CHECK_STR(buf, "\\\"");

    CHECK(sc_json_escape("\\", buf, sizeof(buf)) == 2);
    CHECK_STR(buf, "\\\\");

    CHECK(sc_json_escape("\n", buf, sizeof(buf)) == 2);
    CHECK_STR(buf, "\\n");

    CHECK(sc_json_escape("\x01", buf, sizeof(buf)) == 6);
    CHECK_STR(buf, "\\u0001");

    /* Chinese: 主力机 = U+4E3B U+529B U+673A */
    CHECK(sc_json_escape("\xE4\xB8\xBB\xE5\x8A\x9B\xE6\x9C\xBA", buf, sizeof(buf)) == 18);
    CHECK_STR(buf, "\\u4E3B\\u529B\\u673A");

    /* Emoji U+1F600 becomes a UTF-16 surrogate pair, like .NET does. */
    CHECK(sc_json_escape("\xF0\x9F\x98\x80", buf, sizeof(buf)) == 12);
    CHECK_STR(buf, "\\uD83D\\uDE00");

    /* Invalid UTF-8 must not produce broken JSON. */
    CHECK(sc_json_escape("\xFF", buf, sizeof(buf)) == 6);
    CHECK_STR(buf, "\\uFFFD");

    /* A writer that runs out of room must not overflow and must report oom. */
    sc_json_w_t w;
    char tiny[4];
    sc_json_w_init(&w, tiny, sizeof(tiny));
    sc_json_str(&w, "abcdef");
    CHECK(w.oom);
    CHECK(strlen(tiny) < sizeof(tiny));
}

static void test_display_name(void)
{
    sc_alias_table_t table;
    sc_alias_table_init(&table);
    CHECK(sc_alias_table_add(&table, "iPhone15,3", "主力机"));
    CHECK(sc_alias_table_add(&table, "pixel 7", "备用机"));

    char out[SC_STR_LEN];
    CHECK_STR(sc_display_name(&table, "iPhone15,3", out, sizeof(out)), "主力机");
    /* alias match is ASCII case-insensitive, like StringComparer.OrdinalIgnoreCase */
    CHECK_STR(sc_display_name(&table, "PIXEL 7", out, sizeof(out)), "备用机");
    /* hard-coded iPhone display map survives */
    CHECK_STR(sc_display_name(&table, "iPhone14,3", out, sizeof(out)), "13 Pro Max");
    /* raw model passes through when no alias matches */
    CHECK_STR(sc_display_name(&table, "Pixel 8", out, sizeof(out)), "Pixel 8");
    /* NULL / blank model -> the daemon's placeholder */
    CHECK_STR(sc_display_name(&table, NULL, out, sizeof(out)), "未知机型");
    CHECK_STR(sc_display_name(&table, "   ", out, sizeof(out)), "未知机型");

    /* Blank aliases are rejected at insert time. */
    sc_alias_table_t empty;
    sc_alias_table_init(&empty);
    CHECK(!sc_alias_table_add(&empty, "  ", "x"));
    CHECK(!sc_alias_table_add(&empty, "x", "   "));
}

static void test_route(void)
{
    CHECK(sc_route_resolve("GET", "/status/") == SC_ROUTE_STATUS);
    CHECK(sc_route_resolve("GET", "/health") == SC_ROUTE_HEALTH);
    /* method is case-insensitive */
    CHECK(sc_route_resolve("get", "/health") == SC_ROUTE_HEALTH);
    /* trailing slash is load-bearing */
    CHECK(sc_route_resolve("GET", "/status") == SC_ROUTE_NOT_FOUND);
    CHECK(sc_route_resolve("GET", "/health/") == SC_ROUTE_NOT_FOUND);
    /* path is case-sensitive */
    CHECK(sc_route_resolve("GET", "/Health") == SC_ROUTE_NOT_FOUND);
    CHECK(sc_route_resolve("GET", "/nope") == SC_ROUTE_NOT_FOUND);
    /* non-GET wins even for unknown paths */
    CHECK(sc_route_resolve("POST", "/status/") == SC_ROUTE_METHOD_NOT_ALLOWED);
    CHECK(sc_route_resolve("POST", "/nope") == SC_ROUTE_METHOD_NOT_ALLOWED);
    CHECK(sc_route_resolve("OPTIONS", "/health") == SC_ROUTE_METHOD_NOT_ALLOWED);
    CHECK(sc_route_resolve(NULL, "/status/") == SC_ROUTE_METHOD_NOT_ALLOWED);
}

static void test_hysteresis(void)
{
    sc_probe_state_t state;
    memset(&state, 0, sizeof(state));

    /* one failure is not enough */
    CHECK(sc_hysteresis_apply(&state, false, 100) == false);
    CHECK(state.failures == 1);
    CHECK(!state.has_last_seen);

    /* two consecutive failures flip offline */
    CHECK(sc_hysteresis_apply(&state, false, 200) == false);
    CHECK(state.failures == 2);

    /* a single success comes straight back online */
    CHECK(sc_hysteresis_apply(&state, true, 300) == true);
    CHECK(state.failures == 0);
    CHECK(state.has_last_seen);
    CHECK(state.last_seen_at == 300);

    /* and one isolated failure does not knock it offline again */
    CHECK(sc_hysteresis_apply(&state, false, 400) == true);
    CHECK(sc_hysteresis_apply(&state, false, 500) == false);
}

static void test_health(void)
{
    sc_health_t health;

    sc_health_evaluate_json("{\"schema\":3,\"generatedAt\":123,\"devices\":[]}",
                            strlen("{\"schema\":3,\"generatedAt\":123,\"devices\":[]}"), &health);
    CHECK(health.ok);
    CHECK(health.has_device_count && health.device_count == 0);
    CHECK(health.has_generated_at && health.generated_at == 123);
    CHECK(health.has_schema && health.schema == 3);

    /* devices must be an array */
    sc_health_evaluate_json("{\"devices\":{}}", strlen("{\"devices\":{}}"), &health);
    CHECK(!health.ok);

    /* no devices member at all */
    sc_health_evaluate_json("{\"generatedAt\":1}", strlen("{\"generatedAt\":1}"), &health);
    CHECK(!health.ok);

    /* non-object root */
    sc_health_evaluate_json("[]", strlen("[]"), &health);
    CHECK(!health.ok);

    /* unparsable */
    sc_health_evaluate_json("not json", strlen("not json"), &health);
    CHECK(!health.ok);

    /* missing generatedAt/schema does not block readiness, it just stays null */
    sc_health_evaluate_json("{\"devices\":[]}", strlen("{\"devices\":[]}"), &health);
    CHECK(health.ok);
    CHECK(!health.has_generated_at);
    CHECK(!health.has_schema);

    /* a fractional generatedAt is not an int64 in the daemon's eyes */
    sc_health_evaluate_json("{\"devices\":[],\"generatedAt\":1.5}",
                            strlen("{\"devices\":[],\"generatedAt\":1.5}"), &health);
    CHECK(health.ok);
    CHECK(!health.has_generated_at);
}

static void test_snapshot_empty(void)
{
    sc_snapshot_t snapshot;
    sc_snapshot_init(&snapshot);
    snapshot.generated_at = 0;

    char buf[512];
    size_t len = sc_snapshot_render(&snapshot, buf, sizeof(buf));
    CHECK(len > 0);
    CHECK_STR(buf, "{\"schema\":3,\"generatedAt\":0,\"devices\":[]}");
    CHECK(sc_snapshot_render_len(&snapshot) == len);
}

static void test_snapshot_full_device(void)
{
    sc_snapshot_t snapshot;
    sc_snapshot_init(&snapshot);
    snapshot.generated_at = 1792851926;

    sc_device_t *device = sc_snapshot_append(&snapshot);
    CHECK(device != NULL);
    sc_str_set_raw(&device->id, "iphone");
    sc_str_set_raw(&device->platform, "ios");
    sc_str_set_raw(&device->model, "iPhone15,3");
    sc_config_t cfg;
    sc_config_defaults(&cfg);
    sc_alias_table_add(&cfg.aliases, "iPhone15,3", "主力机");
    char name[SC_STR_LEN];
    sc_str_set_raw(&device->display_name,
                   sc_display_name(&cfg.aliases, device->model.v, name, sizeof(name)));

    device->battery_level.has = true;
    device->battery_level.v = 87;
    device->is_charging.has = true;
    device->is_charging.v = true;
    /* mediaState absent must render as "unknown", not null */
    device->updated_at.has = true;
    device->updated_at.v = 1792851926;

    device->has_transport = true;
    sc_str_set_raw(&device->transport.active, "usb");
    device->transport.degraded.has = true;
    device->transport.degraded.v = false;
    device->transport.online.has = true;
    device->transport.online.v = true;
    device->transport.last_seen_at.has = true;
    device->transport.last_seen_at.v = 1792851900;
    sc_str_set_raw(&device->transport.detail, "usb");

    char buf[2048];
    size_t len = sc_snapshot_render(&snapshot, buf, sizeof(buf));
    CHECK(len > 0);

    const char *expected =
        "{\"schema\":3,\"generatedAt\":1792851926,\"devices\":[{"
        "\"id\":\"iphone\",\"platform\":\"ios\",\"model\":\"iPhone15,3\","
        "\"displayName\":\"\\u4E3B\\u529B\\u673A\",\"batteryLevel\":87,"
        "\"isCharging\":true,\"mediaTitle\":null,\"mediaArtist\":null,"
        "\"mediaState\":\"unknown\",\"foregroundApp\":null,\"foregroundAppName\":null,"
        "\"updatedAt\":1792851926,\"error\":null,"
        "\"transport\":{\"active\":\"usb\",\"degraded\":false,\"online\":true,"
        "\"lastSeenAt\":1792851900,\"detail\":\"usb\"}}]}";
    CHECK_STR(buf, expected);

    /* and the rendered body must be parseable by cJSON */
    cJSON *root = cJSON_ParseWithLength(buf, len);
    CHECK(root != NULL);
    if (root != NULL) {
        CHECK(cJSON_IsArray(cJSON_GetObjectItemCaseSensitive(root, "devices")));
        cJSON_Delete(root);
    }
}

static void test_snapshot_console_and_ble_blocks(void)
{
    sc_snapshot_t snapshot;
    sc_snapshot_init(&snapshot);
    snapshot.generated_at = 100;

    sc_device_t *console = sc_snapshot_append(&snapshot);
    sc_str_set_raw(&console->id, "ps5");
    sc_str_set_raw(&console->platform, "console");
    sc_str_set_raw(&console->model, "PS5");
    sc_str_set_raw(&console->display_name, "PS5");
    console->has_console = true;
    console->console.online.has = true;
    console->console.online.v = false;
    sc_str_set_raw(&console->console.method, "tcp");
    sc_str_set_raw(&console->console.name, "PS5");
    sc_str_set_raw(&console->console.type, "ps5");
    sc_str_set(&console->console.status_text, "离线");
    console->console.idle_hint.has = true;
    console->console.idle_hint.v = true;
    console->has_transport = true;
    sc_str_set_raw(&console->transport.active, "none");
    console->transport.degraded.has = true;
    console->transport.degraded.v = false;
    console->transport.online.has = true;
    console->transport.online.v = false;

    sc_device_t *ble = sc_snapshot_append(&snapshot);
    sc_str_set_raw(&ble->id, "hrm");
    sc_str_set_raw(&ble->platform, "ble");
    sc_str_set_raw(&ble->model, "HRM");
    sc_str_set_raw(&ble->display_name, "HRM");
    ble->has_ble = true;
    sc_str_set_raw(&ble->ble.address, "AA:BB:CC:DD:EE:FF");
    sc_str_set_raw(&ble->ble.name, "HRM");
    ble->ble.rssi.has = true;
    ble->ble.rssi.v = -52;
    sc_strlist_add(&ble->ble.services, "battery");
    sc_strlist_add(&ble->ble.services, "heart_rate");
    ble->ble.connected.has = true;
    ble->ble.connected.v = true;
    ble->ble.heart_rate.has = true;
    ble->ble.heart_rate.v = 72;
    ble->ble.last_seen_at.has = true;
    ble->ble.last_seen_at.v = 99;

    char buf[2048];
    CHECK(sc_snapshot_render(&snapshot, buf, sizeof(buf)) > 0);

    /* console block keys and order */
    const char *console_expected =
        "\"console\":{\"online\":false,\"method\":\"tcp\",\"lastSeenAt\":null,"
        "\"mac\":null,\"idleHint\":true,\"name\":\"PS5\",\"type\":\"ps5\","
        "\"statusText\":\"\\u79BB\\u7EBF\"}";
    CHECK(strstr(buf, console_expected) != NULL);

    /* ble block keys and order */
    const char *ble_expected =
        "\"ble\":{\"address\":\"AA:BB:CC:DD:EE:FF\",\"name\":\"HRM\",\"rssi\":-52,"
        "\"services\":[\"battery\",\"heart_rate\"],\"connected\":true,"
        "\"heartRate\":72,\"statusText\":null,\"lastSeenAt\":99}";
    CHECK(strstr(buf, ble_expected) != NULL);
}

static void test_config_defaults_and_parse(void)
{
    sc_config_t cfg;
    sc_config_defaults(&cfg);
    CHECK(cfg.refresh_seconds == 30);
    CHECK(cfg.console_count == 0);
    CHECK(cfg.ble_count == 0);
    CHECK(!cfg.pc_enabled);
    CHECK(cfg.pc_timeout_seconds == 5);
    CHECK(cfg.aliases.count == 0);

    const char *json =
        "{"
        "\"RefreshIntervalSeconds\":15,"
        "\"Aliases\":{\"PS5\":\"客厅\",\"iPhone15,3\":\"主力机\"},"
        "\"Consoles\":["
        "  {\"id\":\"ps5\",\"name\":\"PS5\",\"type\":\"ps5\",\"ip\":\"192.168.1.50\","
        "   \"offlineText\":\"已关机\",\"tcpPorts\":[9295,9295,3074,0,70000]},"
        "  {\"id\":\"xbox\",\"ip\":\"192.168.1.51\",\"tcpPorts\":[]},"
        "  {\"id\":\"broken\"},"
        "  {\"id\":\"badip\",\"ip\":\"999.1.1.1\"}"
        "],"
        "\"BleDevices\":["
        "  {\"id\":\"hrm\",\"name\":\"HRM\",\"nameFilter\":\"Polar\",\"services\":[\"heart_rate\"],"
        "   \"heartRateWaitSeconds\":6},"
        "  {\"id\":\"buds\"},"
        "  {\"id\":\"airpods\",\"address\":\"AA:BB:CC:DD:EE:FF\",\"services\":[\"nope\"]}"
        "],"
        "\"PcFeed\":{\"enabled\":true,\"url\":\"http://192.168.1.10:5099/status/\","
        "\"timeoutSeconds\":9}"
        "}";

    sc_config_error_t errors[SC_MAX_CONFIG_ERRORS];
    int error_count = sc_config_parse(json, strlen(json), &cfg, errors, SC_MAX_CONFIG_ERRORS);

    CHECK(cfg.refresh_seconds == 15);
    CHECK(cfg.console_count == 2);
    CHECK(cfg.ble_count == 2);
    CHECK(cfg.aliases.count == 2);
    CHECK(cfg.pc_enabled);
    CHECK_STR(cfg.pc_url.v, "http://192.168.1.10:5099/status/");
    CHECK(cfg.pc_timeout_seconds == 9);

    /* tcpPorts: dedupe, drop out-of-range, keep order */
    CHECK(cfg.consoles[0].tcp_port_count == 2);
    CHECK(cfg.consoles[0].tcp_ports[0] == 9295);
    CHECK(cfg.consoles[0].tcp_ports[1] == 3074);
    CHECK_STR(cfg.consoles[0].offline_text.v, "已关机");
    /* an empty list disables the TCP fallback */
    CHECK(cfg.consoles[1].tcp_port_count == 0);
    CHECK_STR(cfg.consoles[1].name.v, "xbox");
    CHECK_STR(cfg.consoles[1].type.v, "console");
    CHECK_STR(cfg.consoles[1].offline_text.v, "离线");

    /* two broken console rows and one broken BLE row */
    CHECK(error_count == 3);
    CHECK_STR(errors[0].platform.v, "console");
    CHECK(strstr(errors[0].message.v, "id 或 ip") != NULL);
    CHECK(strstr(errors[1].message.v, "IP 非法") != NULL);
    CHECK_STR(errors[2].platform.v, "ble");
    CHECK(strstr(errors[2].message.v, "address 或 nameFilter") != NULL);

    /* BLE details */
    CHECK(cfg.ble[0].want_heart_rate);
    CHECK(!cfg.ble[0].want_battery);
    CHECK(cfg.ble[0].heart_rate_wait_seconds == 6);
    /* invalid services fall back to battery */
    CHECK(cfg.ble[1].want_battery);
}

static void test_config_bad_input(void)
{
    sc_config_t cfg;
    CHECK(sc_config_parse(NULL, 0, &cfg, NULL, 0) == 0);
    CHECK(cfg.refresh_seconds == 30);

    CHECK(sc_config_parse("not json", strlen("not json"), &cfg, NULL, 0) == 0);
    CHECK(cfg.refresh_seconds == 30);

    CHECK(sc_config_parse("[]", strlen("[]"), &cfg, NULL, 0) == 0);
    CHECK(cfg.refresh_seconds == 30);

    /* out-of-range interval falls back to the default */
    const char *json = "{\"RefreshIntervalSeconds\":4}";
    CHECK(sc_config_parse(json, strlen(json), &cfg, NULL, 0) == 0);
    CHECK(cfg.refresh_seconds == 30);

    json = "{\"RefreshIntervalSeconds\":\"30\"}";
    CHECK(sc_config_parse(json, strlen(json), &cfg, NULL, 0) == 0);
    CHECK(cfg.refresh_seconds == 30);
}

/* Compare the rendered 13 keys against schema/status-contract.json. */
static void test_contract_key_order(void)
{
#ifdef SC_CONTRACT_PATH
    FILE *handle = fopen(SC_CONTRACT_PATH, "rb");
    if (handle == NULL) {
        printf("SKIP contract test: %s not readable\n", SC_CONTRACT_PATH);
        return;
    }
    (void)fseek(handle, 0, SEEK_END);
    long size = ftell(handle);
    (void)fseek(handle, 0, SEEK_SET);
    if (size <= 0) {
        fclose(handle);
        printf("SKIP contract test: empty file\n");
        return;
    }
    char *text = (char *)malloc((size_t)size + 1);
    if (text == NULL) {
        fclose(handle);
        CHECK(0);
        return;
    }
    size_t read = fread(text, 1, (size_t)size, handle);
    fclose(handle);
    text[read] = '\0';

    cJSON *contract = cJSON_ParseWithLength(text, read);
    free(text);
    CHECK(contract != NULL);
    if (contract == NULL) {
        return;
    }

    cJSON *expected = cJSON_GetObjectItemCaseSensitive(contract, "snapshotDeviceFields");
    CHECK(cJSON_IsArray(expected));

    /* Render one device and read its key order back. */
    sc_snapshot_t snapshot;
    sc_snapshot_init(&snapshot);
    snapshot.generated_at = 1;
    sc_device_t *device = sc_snapshot_append(&snapshot);
    sc_str_set_raw(&device->id, "x");
    sc_str_set_raw(&device->platform, "console");

    char buf[1024];
    size_t len = sc_snapshot_render(&snapshot, buf, sizeof(buf));
    cJSON *rendered = cJSON_ParseWithLength(buf, len);
    CHECK(rendered != NULL);
    if (rendered != NULL && cJSON_IsArray(expected)) {
        cJSON *rows = cJSON_GetObjectItemCaseSensitive(rendered, "devices");
        cJSON *row = cJSON_GetArrayItem(rows, 0);
        int index = 0;
        cJSON *expected_key = NULL;
        cJSON_ArrayForEach(expected_key, expected)
        {
            cJSON *actual_key = cJSON_GetArrayItem(expected, index);
            (void)actual_key;
            if (index == 0) {
                /* walk both lists in lock step */
            }
            index += 1;
        }

        int expected_count = cJSON_GetArraySize(expected);
        int actual_count = 0;
        for (cJSON *item = row->child; item != NULL; item = item->next) {
            actual_count += 1;
        }
        CHECK(actual_count == expected_count);

        index = 0;
        cJSON *actual_item = row->child;
        cJSON_ArrayForEach(expected_key, expected)
        {
            CHECK(actual_item != NULL);
            if (actual_item == NULL) {
                break;
            }
            CHECK_STR(actual_item->string, expected_key->valuestring);
            actual_item = actual_item->next;
            index += 1;
        }
    }
    if (rendered != NULL) {
        cJSON_Delete(rendered);
    }
    cJSON_Delete(contract);
#else
    printf("SKIP contract test: SC_CONTRACT_PATH not defined\n");
#endif
}

/* ---------------------------------------------------------------- fixtures */

static char *read_file(const char *path, size_t *out_len)
{
    FILE *handle = fopen(path, "rb");
    if (handle == NULL) {
        return NULL;
    }
    (void)fseek(handle, 0, SEEK_END);
    long size = ftell(handle);
    (void)fseek(handle, 0, SEEK_SET);
    if (size <= 0) {
        fclose(handle);
        return NULL;
    }
    char *text = (char *)malloc((size_t)size + 1);
    if (text == NULL) {
        fclose(handle);
        return NULL;
    }
    size_t read = fread(text, 1, (size_t)size, handle);
    fclose(handle);
    text[read] = '\0';
    if (out_len != NULL) {
        *out_len = read;
    }
    return text;
}

/* Order-insensitive semantic equality: keys may differ in order, numbers compare
 * by value (the daemon's pet block contains -3.0 which we re-print as -3). */
static bool json_equal(const cJSON *left, const cJSON *right)
{
    if (left == NULL || right == NULL) {
        return left == right;
    }
    if (cJSON_IsObject(left) && cJSON_IsObject(right)) {
        int left_count = 0;
        for (const cJSON *item = left->child; item != NULL; item = item->next) {
            left_count += 1;
            const cJSON *other = cJSON_GetObjectItemCaseSensitive(right, item->string);
            if (other == NULL || !json_equal(item, other)) {
                return false;
            }
        }
        int right_count = 0;
        for (const cJSON *item = right->child; item != NULL; item = item->next) {
            right_count += 1;
        }
        return left_count == right_count;
    }
    if (cJSON_IsArray(left) && cJSON_IsArray(right)) {
        if (cJSON_GetArraySize(left) != cJSON_GetArraySize(right)) {
            return false;
        }
        for (int i = 0; i < cJSON_GetArraySize(left); ++i) {
            if (!json_equal(cJSON_GetArrayItem(left, i), cJSON_GetArrayItem(right, i))) {
                return false;
            }
        }
        return true;
    }
    if (cJSON_IsNumber(left) && cJSON_IsNumber(right)) {
        return left->valuedouble == right->valuedouble;
    }
    if (cJSON_IsString(left) && cJSON_IsString(right)) {
        return strcmp(left->valuestring, right->valuestring) == 0;
    }
    if (cJSON_IsBool(left) && cJSON_IsBool(right)) {
        return cJSON_IsTrue(left) == cJSON_IsTrue(right);
    }
    return cJSON_IsNull(left) && cJSON_IsNull(right);
}

/* Round-trip a real daemon snapshot through parse -> render and require the
 * semantic content to survive. This is the regression net for additive blocks. */
static void test_daemon_fixture_roundtrip(void)
{
#ifdef SC_FIXTURE_DIR
    size_t length = 0;
    char *text = read_file(SC_FIXTURE_DIR "/daemon_status.json", &length);
    if (text == NULL) {
        printf("SKIP daemon fixture test: not found\n");
        return;
    }

    sc_alias_table_t aliases;
    sc_alias_table_init(&aliases);
    sc_device_t rows[SC_MAX_DEVICES];
    int count = sc_wire_parse_devices(text, length, &aliases, rows, SC_MAX_DEVICES);
    CHECK(count == 3);
    if (count != 3) {
        free(text);
        return;
    }

    CHECK_STR(rows[0].id.v, "iphone");
    CHECK_STR(rows[0].platform.v, "ios");
    CHECK(rows[0].error.has);
    CHECK(strstr(rows[0].error.v, "iPhone") != NULL);
    CHECK(rows[0].has_transport);
    CHECK_STR(rows[0].transport.active.v, "none");
    CHECK(!rows[0].has_nekotrak);

    CHECK_STR(rows[2].id.v, "nekotrak");
    CHECK_STR(rows[2].platform.v, "windows");
    CHECK_STR(rows[2].model.v, "NekoTrak 1.2.0");
    CHECK_STR(rows[2].foreground_app.v, "msedge.exe");
    CHECK(!rows[2].error.has);
    CHECK(rows[2].has_transport);
    CHECK_STR(rows[2].transport.active.v, "process");
    CHECK_STR(rows[2].transport.detail.v, "nekotrak");
    CHECK(rows[2].has_nekotrak);
    CHECK_STR(rows[2].nekotrak.version.v, "1.2.0");
    CHECK_STR(rows[2].nekotrak.source.v, "L1");
    CHECK(rows[2].nekotrak.uptime_sec.has && rows[2].nekotrak.uptime_sec.v == 6351);
    /* ISO strings, NOT epoch seconds -- the type bug this fixture caught. */
    CHECK_STR(rows[2].nekotrak.started_at.v, "2026-11-01T09:48:00.680+08:00");
    CHECK_STR(rows[2].nekotrak.last_log_at.v, "2026-11-01T09:48:03.753+08:00");
    CHECK(rows[2].nekotrak.tracker_active.has && rows[2].nekotrak.tracker_active.v);
    CHECK_STR(rows[2].nekotrak.tracker_stopped_at.v, "2026-11-01T02:15:27.000+08:00");
    CHECK(!rows[2].nekotrak.current_game.has);
    CHECK(!rows[2].nekotrak.current_game_app_id.has);
    CHECK(rows[2].nekotrak.has_pet);
    CHECK(!rows[2].nekotrak.has_pomodoro);
    CHECK(rows[2].nekotrak.warns.count == 0);

    /* Render the parsed rows and compare each device semantically with the
     * fixture (key order and whitespace do not matter, values must match). */
    sc_snapshot_t snapshot;
    sc_snapshot_init(&snapshot);
    snapshot.generated_at = 1793504032;
    for (int i = 0; i < count; ++i) {
        snapshot.devices[i] = rows[i];
    }
    snapshot.count = count;

    char buf[8192];
    size_t rendered_len = sc_snapshot_render(&snapshot, buf, sizeof(buf));
    CHECK(rendered_len > 0);
    cJSON *rendered = cJSON_ParseWithLength(buf, rendered_len);
    cJSON *source = cJSON_ParseWithLength(text, length);
    CHECK(rendered != NULL && source != NULL);
    if (rendered != NULL && source != NULL) {
        cJSON *rendered_rows = cJSON_GetObjectItemCaseSensitive(rendered, "devices");
        cJSON *source_rows = cJSON_GetObjectItemCaseSensitive(source, "devices");
        CHECK(cJSON_GetArraySize(rendered_rows) == cJSON_GetArraySize(source_rows));
        for (int i = 0; i < cJSON_GetArraySize(source_rows); ++i) {
            CHECK(json_equal(cJSON_GetArrayItem(source_rows, i),
                             cJSON_GetArrayItem(rendered_rows, i)));
        }
    }
    if (rendered != NULL) {
        cJSON_Delete(rendered);
    }
    if (source != NULL) {
        cJSON_Delete(source);
    }

    /* Stale path must keep the additive blocks that describe identity and drop
     * the per-round ones (transport), exactly like StatusCoordinator. */
    sc_device_t stale = rows[2];
    sc_device_mark_stale(&stale, SC_STALE_ERROR_TEXT);
    CHECK_STR(stale.error.v, SC_STALE_ERROR_TEXT);
    CHECK(!stale.has_transport);
    CHECK(stale.has_nekotrak);
    CHECK_STR(stale.nekotrak.version.v, "1.2.0");
    CHECK(!stale.battery_level.has);
    CHECK(!stale.media_title.has);

    free(text);
#else
    printf("SKIP daemon fixture test: SC_FIXTURE_DIR not defined\n");
#endif
}

/* The route budget is the one number that, when it under-counts, silently drops
 * handlers at runtime with no compile error -- that is how GET/DELETE
 * /control/coredump, GET /control/audit and all seven catch-all handlers once
 * went missing while `idf.py build` stayed green.
 *
 * net/http_server.c and control/control_api.c each _Static_assert their real
 * table against these constants, so the chain is closed from both ends: change a
 * table without changing the header and the firmware build fails; change the
 * header without changing this test and the host suite fails. The literals here
 * are also exactly what docs/guide/http-api.md promises. */
static void test_route_budget(void)
{
    CHECK(SC_HTTP_SPECIFIC_ROUTES == 3);  /* /status/, /health, /diag */
    CHECK(SC_CONTROL_ROUTE_COUNT == 10);  /* the authenticated control routes */
    CHECK(SC_HTTP_CATCHALL_ROUTES == 7);  /* one wildcard entry per method: 405, not 404 */
    CHECK(SC_HTTP_TOTAL_ROUTES == 20);    /* what httpd_start must allocate */
    CHECK(SC_HTTP_TOTAL_ROUTES ==
          SC_HTTP_SPECIFIC_ROUTES + SC_CONTROL_ROUTE_COUNT + SC_HTTP_CATCHALL_ROUTES);
}

/* ============================================================================
 * Regression guards for the defects found in the v3.4.0 bug hunt.
 *
 * Each of these was red against the code as shipped in v3.4.0; they exist so the
 * fixed behaviour cannot silently regress. See
 * docs/test-reports/v3.4.0-bug-hunt.md for the measurements that motivated them.
 * ========================================================================= */

/* Fill a device with the largest nekotrak raw subtree the model allows, which is
 * what a chatty NekoTrak pet produces in practice. */
static void fill_fat_nekotrak(sc_device_t *d)
{
    d->has_nekotrak = true;
    sc_str_set(&d->nekotrak.version, "1.2.0");
    d->nekotrak.has_pet = true;
    memset(d->nekotrak.pet_json, 'P', SC_RAW_JSON_LEN - 1);
    d->nekotrak.pet_json[0] = '{';
    d->nekotrak.pet_json[SC_RAW_JSON_LEN - 2] = '}';
    d->nekotrak.pet_json[SC_RAW_JSON_LEN - 1] = '\0';
}

/* Every string at its maximum, every block present: the true worst case a single
 * row can contribute. */
static void fill_maximal_device(sc_device_t *d, int i)
{
    char tmp[SC_STR_LEN];
    memset(tmp, 'M', sizeof(tmp) - 1);
    tmp[sizeof(tmp) - 1] = '\0';

    snprintf(d->id.v, SC_STR_LEN, "device-%02d", i);
    d->id.has = true;
    sc_str_set_raw(&d->platform, "android");
    sc_str_set_raw(&d->model, tmp);
    sc_str_set_raw(&d->display_name, tmp);
    d->battery_level.has = true;
    d->battery_level.v = 100;
    d->is_charging.has = true;
    d->is_charging.v = true;
    sc_str_set_raw(&d->media_title, tmp);
    sc_str_set_raw(&d->media_artist, tmp);
    sc_str_set_raw(&d->media_state, "playing");
    sc_str_set_raw(&d->foreground_app, tmp);
    sc_str_set_raw(&d->foreground_app_name, tmp);
    d->updated_at.has = true;
    d->updated_at.v = 1794032391;
    sc_str_set_raw(&d->error, tmp);

    d->has_console = true;
    d->console.online.has = true;
    d->console.online.v = true;
    sc_str_set_raw(&d->console.method, "ping");
    d->console.last_seen_at.has = true;
    d->console.last_seen_at.v = 1794032391;
    sc_str_set_raw(&d->console.mac, tmp);
    d->console.idle_hint.has = true;
    d->console.idle_hint.v = true;
    sc_str_set_raw(&d->console.name, tmp);
    sc_str_set_raw(&d->console.type, tmp);
    sc_str_set_raw(&d->console.status_text, tmp);

    d->has_node = true;
    d->node.ssh.has = true;
    d->node.ssh.v = true;
    d->node.online.has = true;
    d->node.online.v = true;
    sc_str_set_raw(&d->node.status_text, tmp);
    sc_str_set_raw(&d->node.method, "ssh");
    sc_str_set_raw(&d->node.source, "ssh");
    sc_str_set_raw(&d->node.hostname, tmp);
    d->node.last_seen_at.has = true;
    d->node.last_seen_at.v = 1794032391;
    d->node.uptime_seconds.has = true;
    d->node.uptime_seconds.v = 999999;

    d->has_transport = true;
    sc_str_set_raw(&d->transport.active, "usb");
    d->transport.degraded.has = true;
    d->transport.degraded.v = false;
    d->transport.online.has = true;
    d->transport.online.v = true;
    d->transport.last_seen_at.has = true;
    d->transport.last_seen_at.v = 1794032391;
    sc_str_set_raw(&d->transport.detail, tmp);

    d->has_ble = true;
    sc_str_set_raw(&d->ble.address, "AA:BB:CC:DD:EE:FF");
    sc_str_set_raw(&d->ble.name, tmp);
    d->ble.rssi.has = true;
    d->ble.rssi.v = -100;
    for (int k = 0; k < SC_MAX_LIST_ITEMS; ++k) {
        (void)sc_strlist_add(&d->ble.services, k % 2 ? "heart_rate" : "battery");
    }
    d->ble.connected.has = true;
    d->ble.connected.v = true;
    d->ble.heart_rate.has = true;
    d->ble.heart_rate.v = 200;
    sc_str_set_raw(&d->ble.status_text, tmp);
    d->ble.last_seen_at.has = true;
    d->ble.last_seen_at.v = 1794032391;

    d->has_nekotrak = true;
    sc_str_set_raw(&d->nekotrak.version, tmp);
    sc_str_set_raw(&d->nekotrak.source, tmp);
    d->nekotrak.uptime_sec.has = true;
    d->nekotrak.uptime_sec.v = 999999;
    sc_str_set_raw(&d->nekotrak.started_at, "2026-11-07T00:00:00.000+08:00");
    sc_str_set_raw(&d->nekotrak.last_log_at, "2026-11-07T00:00:00.000+08:00");
    d->nekotrak.tracker_active.has = true;
    d->nekotrak.tracker_active.v = true;
    sc_str_set_raw(&d->nekotrak.tracker_started_at, "2026-11-07T00:00:00.000+08:00");
    sc_str_set_raw(&d->nekotrak.tracker_stopped_at, "2026-11-07T00:00:00.000+08:00");
    sc_str_set_raw(&d->nekotrak.current_game, tmp);
    d->nekotrak.current_game_app_id.has = true;
    d->nekotrak.current_game_app_id.v = 2147483647;
    fill_fat_nekotrak(d);
    d->nekotrak.has_pomodoro = true;
    memset(d->nekotrak.pomodoro_json, 'Q', SC_RAW_JSON_LEN - 1);
    d->nekotrak.pomodoro_json[0] = '{';
    d->nekotrak.pomodoro_json[SC_RAW_JSON_LEN - 2] = '}';
    d->nekotrak.pomodoro_json[SC_RAW_JSON_LEN - 1] = '\0';
    for (int k = 0; k < SC_MAX_LIST_ITEMS; ++k) {
        (void)sc_strlist_add(&d->nekotrak.warns, tmp);
    }
}

static sc_device_t *append_plain_device(sc_snapshot_t *snap, int i)
{
    sc_device_t *d = sc_snapshot_append(snap);
    if (d == NULL) {
        return NULL;
    }
    char tmp[64];
    snprintf(tmp, sizeof(tmp), "device-%02d", i);
    sc_str_set(&d->id, tmp);
    sc_str_set(&d->platform, "android");
    sc_str_set(&d->model, "Pixel 7");
    sc_str_set(&d->media_state, "unknown");
    d->battery_level.has = true;
    d->battery_level.v = 42;
    d->updated_at.has = true;
    d->updated_at.v = snap->generated_at;
    return d;
}

/* BUG-1  Snapshot capacity. SC_MAX_DEVICES is 16 and the firmware renders into
 * CONFIG_SC_MAX_SNAPSHOT_JSON, which must be at least SC_SNAPSHOT_JSON_BUDGET
 * (a compile-time assert in app_main.c and http_server.c enforces that half).
 * This half proves the budget is actually enough for a full device list. */
static void test_bug_snapshot_capacity(void)
{
    sc_snapshot_t snap;
    sc_snapshot_init(&snap);
    snap.generated_at = 1794032391;
    for (int i = 0; i < SC_MAX_DEVICES; ++i) {
        sc_device_t *d = append_plain_device(&snap, i);
        if (d == NULL) {
            break;
        }
        fill_fat_nekotrak(d);
    }
    static char buf[SC_SNAPSHOT_JSON_BUDGET];
    size_t len = sc_snapshot_render(&snap, buf, sizeof(buf));
    if (len == 0) {
        printf("BUG-1 snapshot capacity: %d devices with maximal nekotrak subtrees do not fit"
               " the %d-byte budget (needed ~%zu)\n",
               SC_MAX_DEVICES, SC_SNAPSHOT_JSON_BUDGET, sc_snapshot_render_len(&snap));
    }
    CHECK(len != 0);
    CHECK(snap.count == SC_MAX_DEVICES);
}

/* BUG-1 (worst case)  Every field of every row at its maximum, every additive
 * block present. This is the number the budget has to cover, so it is measured
 * rather than assumed. */
static void test_worst_case_fits_budget(void)
{
    sc_snapshot_t snap;
    sc_snapshot_init(&snap);
    snap.generated_at = 1794032391;
    for (int i = 0; i < SC_MAX_DEVICES; ++i) {
        sc_device_t *d = sc_snapshot_append(&snap);
        if (d == NULL) {
            break;
        }
        fill_maximal_device(d, i);
    }
    size_t needed = sc_snapshot_render_len(&snap);
    static char buf[SC_SNAPSHOT_JSON_BUDGET];
    size_t len = sc_snapshot_render(&snap, buf, sizeof(buf));
    if (len == 0) {
        printf("BUG-1 worst case: %d fully populated rows need %zu bytes but the budget is %d;"
               " raise SC_SNAPSHOT_JSON_BUDGET (core/sc_limits.h) and the Kconfig default\n",
               SC_MAX_DEVICES, needed, SC_SNAPSHOT_JSON_BUDGET);
    }
    CHECK(len != 0);
    if (len != 0) {
        printf("(worst case measured: %zu of %d budgeted bytes)\n", len, SC_SNAPSHOT_JSON_BUDGET);
    }
}

/* BUG-2  Raw-JSON passthrough: a nekotrak pet/pomodoro subtree whose serialized
 * form exceeds SC_RAW_JSON_LEN-1 bytes is dropped ENTIRELY -- not truncated, not
 * flagged. The real pet on the reference machine is already 309 of ~447 bytes,
 * so ~138 bytes of headroom stand between the current output and silent loss. */
static void test_bug_raw_passthrough_drop(void)
{
    /* pet with a 430-byte tale: serialized subtree is 441 bytes, still kept. */
    char json[2048];
    int p = snprintf(json, sizeof(json),
                     "{\"devices\":[{\"id\":\"nekotrak\",\"platform\":\"windows\","
                     "\"nekotrak\":{\"version\":\"1.2.0\",\"pet\":{\"tale\":\"");
    for (int i = 0; i < 440; ++i) { /* one step past the ceiling */
        json[p++] = 'a';
    }
    p += snprintf(json + p, sizeof(json) - (size_t)p, "\"}}}]}");
    json[p] = '\0';

    sc_alias_table_t al;
    sc_alias_table_init(&al);
    sc_device_t rows[SC_MAX_DEVICES];
    int n = sc_wire_parse_devices(json, strlen(json), &al, rows, SC_MAX_DEVICES);
    CHECK(n == 1);
    if (n != 1) {
        return;
    }
    if (!rows[0].nekotrak.has_pet) {
        printf("BUG-2 raw passthrough: a 440-byte pet tale is silently dropped"
               " (cap is %d bytes, real pet on the reference box is already 309)\n",
               SC_RAW_JSON_LEN - 1);
    }
    CHECK(rows[0].nekotrak.has_pet);
}

/* BUG-3  Truncation at SC_STR_LEN splits a UTF-8 sequence, and the escaper then
 * emits one U+FFFD per leftover byte: a 95-byte cut of 3-byte CJK yields 31 real
 * characters plus two replacement characters, and a cut of 4-byte emoji yields
 * three. Output stays valid JSON but the content is corrupted and inflated. */
static void test_bug_utf8_truncation_mojibake(void)
{
    char big[512];
    int i = 0;
    for (int k = 0; k < 32; ++k) { /* "日" x32 = 96 bytes, one byte past the store */
        memcpy(big + i, "\xe6\x97\xa5", 3);
        i += 3;
    }
    big[i] = '\0';

    sc_str_t s;
    sc_str_clear(&s);
    sc_str_set(&s, big);

    char esc[512];
    sc_json_escape(s.v, esc, sizeof(esc));
    int replaced = 0;
    for (const char *q = esc; (q = strstr(q, "\\uFFFD")) != NULL; ++q) {
        replaced += 1;
    }
    if (replaced != 0) {
        printf("BUG-3 utf8 truncation: %d replacement char(s) injected by the 95-byte"
               " cut; stored tail bytes were a partial UTF-8 sequence\n", replaced);
    }
    CHECK(replaced == 0);
}

/* BUG-4  sc_str_set claims ".NET-style Trim()", but .NET trims every Unicode
 * whitespace rune while this trims ASCII only. A field of U+00A0 (or U+3000)
 * therefore stays present here and becomes null on the daemon -- the two
 * implementations disagree about the same input. */
static void test_bug_unicode_trim(void)
{
    sc_str_t nbsp;
    sc_str_clear(&nbsp);
    sc_str_set(&nbsp, "\xc2\xa0\xc2\xa0"); /* NBSP NBSP */
    if (nbsp.has) {
        printf("BUG-4 trim: a NBSP-only string stays present (%zu bytes);"
               " .NET string.Trim() makes it empty -> null\n", strlen(nbsp.v));
    }
    CHECK(!nbsp.has);

    sc_str_t wide;
    sc_str_clear(&wide);
    sc_str_set(&wide, "\xe3\x80\x80\xe3\x80\x80"); /* U+3000 U+3000 */
    if (wide.has) {
        printf("BUG-4 trim: an ideographic-space-only string stays present (%zu bytes);"
               " .NET trims it\n", strlen(wide.v));
    }
    CHECK(!wide.has);
}

/* BUG-5  /health readiness across the two implementations. sc_health_evaluate_json
 * is documented as mirroring StatusServer.cs, but it parses with cJSON, which
 * tolerates trailing garbage. The daemon (System.Text.Json) rejects it. Measured:
 * '{"devices":[]} trailing' -> daemon 503, firmware 200. */
static void test_bug_health_trailing_garbage(void)
{
    static const char trailing[] = "{\"devices\":[]} trailing";
    sc_health_t h;
    memset(&h, 0, sizeof(h));
    sc_health_evaluate_json(trailing, strlen(trailing), &h);
    if (h.ok) {
        printf("BUG-5 health parity: trailing garbage is accepted here but the daemon"
               " answers 503 for the same body\n");
    }
    CHECK(!h.ok);
}

/* ---------------------------------------------------------------- status LED */

/* The palette and the name mapping are the two things NekoTool and the docs
 * depend on, and they used to live inside an ESP-IDF-only file. */
static void test_led_palette(void)
{
    uint8_t r, g, b;
    sc_led_rgb(SC_LED_OFF, &r, &g, &b);
    CHECK(r == 0 && g == 0 && b == 0);
    sc_led_rgb(SC_LED_OK, &r, &g, &b);
    CHECK(r == 0 && g > 0 && b == 0);
    sc_led_rgb(SC_LED_DEGRADED, &r, &g, &b);
    CHECK(r > 0 && g > 0 && b == 0);
    CHECK(g > r); /* amber, not yellow-white */
    sc_led_rgb(SC_LED_ERROR, &r, &g, &b);
    CHECK(r > 0 && g == 0 && b == 0);

    CHECK_STR(sc_led_name(SC_LED_OFF), "off");
    CHECK_STR(sc_led_name(SC_LED_OK), "green");
    CHECK_STR(sc_led_name(SC_LED_DEGRADED), "amber");
    CHECK_STR(sc_led_name(SC_LED_ERROR), "red");

    /* NULL out-params are tolerated: the painter always passes all three. */
    sc_led_rgb(SC_LED_OK, NULL, NULL, NULL);
}

/* POST /control/led accepts exactly these four spellings. */
static void test_led_name_parsing(void)
{
    sc_led_state_t state = SC_LED_OFF;
    CHECK(sc_led_state_from_name("green", &state) && state == SC_LED_OK);
    CHECK(sc_led_state_from_name("amber", &state) && state == SC_LED_DEGRADED);
    CHECK(sc_led_state_from_name("red", &state) && state == SC_LED_ERROR);
    CHECK(sc_led_state_from_name("off", &state) && state == SC_LED_OFF);

    CHECK(!sc_led_state_from_name("blue", &state));
    CHECK(!sc_led_state_from_name("", &state));
    CHECK(!sc_led_state_from_name(NULL, &state));
    CHECK(!sc_led_state_from_name("green", NULL));
    /* undocumented spellings must not silently work */
    CHECK(!sc_led_state_from_name("Green", &state));
    CHECK(!sc_led_state_from_name("green ", &state));
}

/* Issue #7, case 1: while an override is in force the reported colour is the
 * override, and the mode says so. The old code reported "off" because a manual
 * paint stamped the state machine as OFF. */
static void test_led_manual_override(void)
{
    sc_led_model_t m;
    sc_led_model_init(&m, true);
    CHECK(sc_led_model_shown(&m) == SC_LED_OFF);
    CHECK_STR(sc_led_model_mode_name(&m), "auto");

    CHECK(sc_led_model_set_auto(&m, SC_LED_OK)); /* off -> green */
    CHECK(sc_led_model_shown(&m) == SC_LED_OK);
    CHECK(!sc_led_model_set_auto(&m, SC_LED_OK)); /* same colour, no repaint */

    CHECK(sc_led_model_set_manual(&m, true, SC_LED_ERROR));
    CHECK(sc_led_model_shown(&m) == SC_LED_ERROR);
    CHECK_STR(sc_led_model_mode_name(&m), "manual");

    /* The automatic machine keeps running underneath; it must not paint over. */
    CHECK(!sc_led_model_set_auto(&m, SC_LED_DEGRADED));
    CHECK(sc_led_model_shown(&m) == SC_LED_ERROR);

    /* Releasing the override restores the last automatic colour at once. */
    CHECK(sc_led_model_set_manual(&m, false, SC_LED_OFF));
    CHECK(sc_led_model_shown(&m) == SC_LED_DEGRADED);
    CHECK_STR(sc_led_model_mode_name(&m), "auto");
}

/* Issue #7, case 2 -- the part the issue did not spot: releasing an override
 * while the automatic state is still OFF used to leave the pixel showing the
 * manual colour forever, because the painter skipped the repaint when its cached
 * state had not changed. */
static void test_led_manual_release_to_off(void)
{
    sc_led_model_t m;
    sc_led_model_init(&m, true);

    CHECK(!sc_led_model_set_auto(&m, SC_LED_OFF)); /* already off */
    CHECK(sc_led_model_set_manual(&m, true, SC_LED_OK));
    CHECK(sc_led_model_shown(&m) == SC_LED_OK);

    /* Must ask for a repaint, and must land back on OFF. */
    CHECK(sc_led_model_set_manual(&m, false, SC_LED_OFF));
    CHECK(sc_led_model_shown(&m) == SC_LED_OFF);
    CHECK_STR(sc_led_model_mode_name(&m), "auto");

    /* Idempotent: releasing twice changes nothing and asks for nothing. */
    CHECK(!sc_led_model_set_manual(&m, false, SC_LED_OFF));
}

/* A board without a working strip still has to answer /diag truthfully. */
static void test_led_unavailable(void)
{
    sc_led_model_t m;
    sc_led_model_init(&m, false);
    CHECK(!m.available);
    (void)sc_led_model_set_auto(&m, SC_LED_OK); /* no crash, state still tracked */
    CHECK(sc_led_model_shown(&m) == SC_LED_OK);
}

/* ------------------------------------------------------------ round budget */

/* R1: one round must always be able to finish before the next one starts, and
 * must always finish inside the 30 s task watchdog. Both bounds are asserted
 * across the whole configurable period range, because the failure modes are
 * silent: overlapping rounds stop the snapshot from advancing, and an over-long
 * round would trip the watchdog and reboot the board. */
static void test_round_budget(void)
{
    CHECK(sc_round_budget_ms(5) == 3000);      /* period - 2 s margin */
    CHECK(sc_round_budget_ms(6) == 4000);
    CHECK(sc_round_budget_ms(30) == 25000);    /* capped well below the 30 s period */
    CHECK(sc_round_budget_ms(60) == 25000);
    CHECK(sc_round_budget_ms(3600) == 25000);

    /* A period below the clamp still yields a usable budget rather than zero. */
    CHECK(sc_round_budget_ms(1) == 3000);
    CHECK(sc_round_budget_ms(0) == 3000);

    /* Invariants over the whole documented range (5..3600). Counted, not
     * asserted per iteration: 3600 periods would drown the suite's check count. */
    int violations = 0;
    for (int period = 1; period <= 3600; ++period) {
        int64_t budget = sc_round_budget_ms(period);
        if (budget < 3000 || budget > 25000) {
            violations += 1;
        }
        /* A round must never be allowed to run into the next one. */
        if (period >= 5 && budget >= (int64_t)period * 1000) {
            violations += 1;
        }
    }
    if (violations != 0) {
        printf("round budget: %d period(s) produced an invalid budget\n", violations);
    }
    CHECK(violations == 0);

    /* Deadline comparison is inclusive: reaching it counts as expired. */
    CHECK(!sc_deadline_reached(1000, 999));
    CHECK(sc_deadline_reached(1000, 1000));
    CHECK(sc_deadline_reached(1000, 1001));
    /* A zero deadline is already expired, which is what a starved round sees. */
    CHECK(sc_deadline_reached(0, 0));
}

int main(void)
{
    test_json_escape();
    test_display_name();
    test_route();
    test_route_budget();
    test_bug_snapshot_capacity();
    test_worst_case_fits_budget();
    test_bug_raw_passthrough_drop();
    test_bug_utf8_truncation_mojibake();
    test_bug_unicode_trim();
    test_bug_health_trailing_garbage();
    test_led_palette();
    test_led_name_parsing();
    test_led_manual_override();
    test_led_manual_release_to_off();
    test_led_unavailable();
    test_round_budget();
    test_hysteresis();
    test_health();
    test_snapshot_empty();
    test_snapshot_full_device();
    test_snapshot_console_and_ble_blocks();
    test_config_defaults_and_parse();
    test_config_bad_input();
    test_contract_key_order();
    test_daemon_fixture_roundtrip();

    printf("\n%d checks, %d failures\n", g_checks, g_failures);
    return g_failures == 0 ? 0 : 1;
}
