#include "sc_wire_parse.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"

static const cJSON *get(const cJSON *object, const char *key)
{
    if (object == NULL) {
        return NULL;
    }
    return cJSON_GetObjectItemCaseSensitive(object, key);
}

static void take_str(const cJSON *object, const char *key, sc_str_t *target)
{
    const cJSON *item = get(object, key);
    /* The daemon trims and turns blanks into null; mirror that. */
    if (item != NULL && cJSON_IsString(item)) {
        sc_str_set(target, item->valuestring);
    } else {
        sc_str_clear(target);
    }
}

static void take_i32(const cJSON *object, const char *key, sc_i32_t *target)
{
    const cJSON *item = get(object, key);
    target->has = false;
    target->v = 0;
    if (item == NULL || !cJSON_IsNumber(item)) {
        return;
    }
    double raw = item->valuedouble;
    if (raw != (double)(long long)raw) {
        return;
    }
    if (raw < -2147483648.0 || raw > 2147483647.0) {
        return;
    }
    target->v = (int32_t)raw;
    target->has = true;
}

static void take_i64(const cJSON *object, const char *key, sc_i64_t *target)
{
    const cJSON *item = get(object, key);
    target->has = false;
    target->v = 0;
    if (item == NULL || !cJSON_IsNumber(item)) {
        return;
    }
    double raw = item->valuedouble;
    if (raw != (double)(long long)raw) {
        return;
    }
    target->v = (int64_t)raw;
    target->has = true;
}

static void take_bool(const cJSON *object, const char *key, sc_bool_t *target)
{
    const cJSON *item = get(object, key);
    target->has = false;
    target->v = false;
    if (item == NULL || !cJSON_IsBool(item)) {
        return;
    }
    target->v = cJSON_IsTrue(item) != 0;
    target->has = true;
}

static void take_list(const cJSON *object, const char *key, sc_strlist_t *target)
{
    sc_strlist_clear(target);
    const cJSON *item = get(object, key);
    if (item == NULL || !cJSON_IsArray(item)) {
        return;
    }
    const cJSON *element = NULL;
    cJSON_ArrayForEach(element, item)
    {
        if (cJSON_IsString(element)) {
            (void)sc_strlist_add(target, element->valuestring);
        }
    }
}

/* pet/pomodoro are opaque JSON subtrees in the daemon (cloned as JsonElement).
 * We re-print them compactly; if they do not fit the fixed buffer the member is
 * dropped instead of embedding a truncated (invalid) subtree. Returns true when
 * that happened, so the caller can say so instead of losing the data silently. */
static bool take_raw_object(const cJSON *object, const char *key, bool *has, char *buf, size_t cap)
{
    *has = false;
    buf[0] = '\0';
    const cJSON *item = get(object, key);
    if (item == NULL || !cJSON_IsObject(item)) {
        return false;
    }
    char *printed = cJSON_PrintUnformatted(item);
    if (printed == NULL) {
        return true;
    }
    size_t length = strlen(printed);
    if (length >= cap) {
        free(printed);
        return true;
    }
    memcpy(buf, printed, length + 1);
    free(printed);
    *has = true;
    return false;
}

static void parse_console(const cJSON *object, sc_console_block_t *block)
{
    take_bool(object, "online", &block->online);
    take_str(object, "method", &block->method);
    take_i64(object, "lastSeenAt", &block->last_seen_at);
    take_str(object, "mac", &block->mac);
    take_bool(object, "idleHint", &block->idle_hint);
    take_str(object, "name", &block->name);
    take_str(object, "type", &block->type);
    take_str(object, "statusText", &block->status_text);
}

static void parse_node(const cJSON *object, sc_node_block_t *block)
{
    take_bool(object, "ssh", &block->ssh);
    take_bool(object, "online", &block->online);
    take_str(object, "statusText", &block->status_text);
    take_str(object, "method", &block->method);
    take_str(object, "source", &block->source);
    take_str(object, "hostname", &block->hostname);
    take_i64(object, "lastSeenAt", &block->last_seen_at);
    take_i64(object, "uptimeSeconds", &block->uptime_seconds);
}

static void parse_transport(const cJSON *object, sc_transport_block_t *block)
{
    take_str(object, "active", &block->active);
    take_bool(object, "degraded", &block->degraded);
    take_bool(object, "online", &block->online);
    take_i64(object, "lastSeenAt", &block->last_seen_at);
    take_str(object, "detail", &block->detail);
}

static void parse_ble(const cJSON *object, sc_ble_block_t *block)
{
    take_str(object, "address", &block->address);
    take_str(object, "name", &block->name);
    take_i32(object, "rssi", &block->rssi);
    take_list(object, "services", &block->services);
    take_bool(object, "connected", &block->connected);
    take_i32(object, "heartRate", &block->heart_rate);
    take_str(object, "statusText", &block->status_text);
    take_i64(object, "lastSeenAt", &block->last_seen_at);
}

static void parse_nekotrak(const cJSON *object, sc_nekotrak_block_t *block)
{
    take_str(object, "version", &block->version);
    take_str(object, "source", &block->source);
    take_i64(object, "uptimeSec", &block->uptime_sec);
    take_str(object, "startedAt", &block->started_at);
    take_str(object, "lastLogAt", &block->last_log_at);
    take_bool(object, "trackerActive", &block->tracker_active);
    take_str(object, "trackerStartedAt", &block->tracker_started_at);
    take_str(object, "trackerStoppedAt", &block->tracker_stopped_at);
    take_str(object, "currentGame", &block->current_game);
    take_i32(object, "currentGameAppId", &block->current_game_app_id);

    /* Borrow the daemon's own warns channel instead of dropping the payload in
     * silence: a consumer that never sees `pet` can at least see why. The flags
     * are collected before take_list() because that call resets the list. */
    bool pet_overflowed = take_raw_object(object, "pet", &block->has_pet,
                                          block->pet_json, sizeof(block->pet_json));
    bool pomodoro_overflowed = take_raw_object(object, "pomodoro", &block->has_pomodoro,
                                               block->pomodoro_json,
                                               sizeof(block->pomodoro_json));
    take_list(object, "warns", &block->warns);
    if (pet_overflowed) {
        (void)sc_strlist_add(&block->warns, "nekotrak.pet 超出缓冲已省略");
    }
    if (pomodoro_overflowed) {
        (void)sc_strlist_add(&block->warns, "nekotrak.pomodoro 超出缓冲已省略");
    }
}

static void parse_device(const cJSON *row, const sc_alias_table_t *aliases, sc_device_t *device)
{
    sc_device_init(device);

    take_str(row, "id", &device->id);
    take_str(row, "platform", &device->platform);
    take_str(row, "model", &device->model);
    take_str(row, "displayName", &device->display_name);
    if (!device->display_name.has) {
        /* Fall back to the daemon rule when the producer left it out. */
        char fallback[SC_STR_LEN];
        const char *model = device->model.has ? device->model.v : NULL;
        sc_str_set_raw(&device->display_name,
                       sc_display_name(aliases, model, fallback, sizeof(fallback)));
    }
    take_i32(row, "batteryLevel", &device->battery_level);
    take_bool(row, "isCharging", &device->is_charging);
    take_str(row, "mediaTitle", &device->media_title);
    take_str(row, "mediaArtist", &device->media_artist);
    take_str(row, "mediaState", &device->media_state);
    take_str(row, "foregroundApp", &device->foreground_app);
    take_str(row, "foregroundAppName", &device->foreground_app_name);
    take_i64(row, "updatedAt", &device->updated_at);
    take_str(row, "error", &device->error);

    const cJSON *block = get(row, "console");
    if (block != NULL && cJSON_IsObject(block)) {
        device->has_console = true;
        parse_console(block, &device->console);
    }
    block = get(row, "node");
    if (block != NULL && cJSON_IsObject(block)) {
        device->has_node = true;
        parse_node(block, &device->node);
    }
    block = get(row, "transport");
    if (block != NULL && cJSON_IsObject(block)) {
        device->has_transport = true;
        parse_transport(block, &device->transport);
    }
    block = get(row, "ble");
    if (block != NULL && cJSON_IsObject(block)) {
        device->has_ble = true;
        parse_ble(block, &device->ble);
    }
    block = get(row, "nekotrak");
    if (block != NULL && cJSON_IsObject(block)) {
        device->has_nekotrak = true;
        parse_nekotrak(block, &device->nekotrak);
    }
}

int sc_wire_parse_devices(const char *json, size_t len, const sc_alias_table_t *aliases,
                          sc_device_t *out, int max_devices)
{
    if (json == NULL || len == 0 || out == NULL || max_devices <= 0) {
        return -1;
    }
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL) {
        return -1;
    }
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return -1;
    }
    const cJSON *devices = cJSON_GetObjectItemCaseSensitive(root, "devices");
    if (devices == NULL || !cJSON_IsArray(devices)) {
        cJSON_Delete(root);
        return -1;
    }

    int count = 0;
    const cJSON *row = NULL;
    cJSON_ArrayForEach(row, devices)
    {
        if (count >= max_devices) {
            break;
        }
        if (!cJSON_IsObject(row)) {
            continue;
        }
        parse_device(row, aliases, &out[count]);
        count += 1;
    }
    cJSON_Delete(root);
    return count;
}

int sc_snapshot_merge(sc_snapshot_t *snapshot, const sc_device_t *rows, int count)
{
    if (snapshot == NULL || rows == NULL || count <= 0) {
        return 0;
    }
    int appended = 0;
    for (int i = 0; i < count; ++i) {
        if (snapshot->count >= SC_MAX_DEVICES) {
            break;
        }
        snapshot->devices[snapshot->count] = rows[i];
        snapshot->count += 1;
        appended += 1;
    }
    return appended;
}

void sc_device_mark_stale(sc_device_t *device, const char *error_text)
{
    if (device == NULL) {
        return;
    }
    device->battery_level.has = false;
    device->is_charging.has = false;
    device->media_title.has = false;
    device->media_artist.has = false;
    sc_str_set_raw(&device->media_state, "unknown");
    device->foreground_app.has = false;
    device->foreground_app_name.has = false;
    /* transport describes "the channel used this round"; a stale row has none. */
    device->has_transport = false;
    sc_str_set(&device->error,
               (error_text != NULL && error_text[0] != '\0') ? error_text : SC_STALE_ERROR_TEXT);
}
