#include "sc_snapshot.h"

#include <string.h>

#include "sc_json.h"

static void opt_str(sc_json_w_t *w, const sc_str_t *s)
{
    sc_json_opt_str(w, s->v, s->has);
}

static void kv_str(sc_json_w_t *w, const char *key, const sc_str_t *s)
{
    sc_json_key(w, key);
    opt_str(w, s);
}

static void kv_i64(sc_json_w_t *w, const char *key, const sc_i64_t *v)
{
    sc_json_key(w, key);
    if (v->has) {
        sc_json_int(w, v->v);
    } else {
        sc_json_null(w);
    }
}

static void kv_i32(sc_json_w_t *w, const char *key, const sc_i32_t *v)
{
    sc_json_key(w, key);
    if (v->has) {
        sc_json_int(w, v->v);
    } else {
        sc_json_null(w);
    }
}

static void kv_bool(sc_json_w_t *w, const char *key, const sc_bool_t *v)
{
    sc_json_key(w, key);
    if (v->has) {
        sc_json_bool(w, v->v);
    } else {
        sc_json_null(w);
    }
}

static void render_console(sc_json_w_t *w, const sc_console_block_t *b)
{
    sc_json_raw(w, ",\"console\":{");
    kv_bool(w, "online", &b->online);
    sc_json_raw(w, ",");
    kv_str(w, "method", &b->method);
    sc_json_raw(w, ",");
    kv_i64(w, "lastSeenAt", &b->last_seen_at);
    sc_json_raw(w, ",");
    kv_str(w, "mac", &b->mac);
    sc_json_raw(w, ",");
    kv_bool(w, "idleHint", &b->idle_hint);
    sc_json_raw(w, ",");
    kv_str(w, "name", &b->name);
    sc_json_raw(w, ",");
    kv_str(w, "type", &b->type);
    sc_json_raw(w, ",");
    kv_str(w, "statusText", &b->status_text);
    sc_json_byte(w, '}');
}

static void render_node(sc_json_w_t *w, const sc_node_block_t *b)
{
    sc_json_raw(w, ",\"node\":{");
    kv_bool(w, "ssh", &b->ssh);
    sc_json_raw(w, ",");
    kv_bool(w, "online", &b->online);
    sc_json_raw(w, ",");
    kv_str(w, "statusText", &b->status_text);
    sc_json_raw(w, ",");
    kv_str(w, "method", &b->method);
    sc_json_raw(w, ",");
    kv_str(w, "source", &b->source);
    sc_json_raw(w, ",");
    kv_str(w, "hostname", &b->hostname);
    sc_json_raw(w, ",");
    kv_i64(w, "lastSeenAt", &b->last_seen_at);
    sc_json_raw(w, ",");
    kv_i64(w, "uptimeSeconds", &b->uptime_seconds);
    sc_json_byte(w, '}');
}

static void render_transport(sc_json_w_t *w, const sc_transport_block_t *b)
{
    sc_json_raw(w, ",\"transport\":{");
    kv_str(w, "active", &b->active);
    sc_json_raw(w, ",");
    kv_bool(w, "degraded", &b->degraded);
    sc_json_raw(w, ",");
    kv_bool(w, "online", &b->online);
    sc_json_raw(w, ",");
    kv_i64(w, "lastSeenAt", &b->last_seen_at);
    sc_json_raw(w, ",");
    kv_str(w, "detail", &b->detail);
    sc_json_byte(w, '}');
}

static void render_string_list(sc_json_w_t *w, const sc_strlist_t *list)
{
    sc_json_byte(w, '[');
    for (int i = 0; i < list->count; ++i) {
        if (i > 0) {
            sc_json_byte(w, ',');
        }
        sc_json_str(w, list->items[i]);
    }
    sc_json_byte(w, ']');
}

static void render_ble(sc_json_w_t *w, const sc_ble_block_t *b)
{
    sc_json_raw(w, ",\"ble\":{");
    kv_str(w, "address", &b->address);
    sc_json_raw(w, ",");
    kv_str(w, "name", &b->name);
    sc_json_raw(w, ",");
    kv_i32(w, "rssi", &b->rssi);
    sc_json_raw(w, ",\"services\":");
    render_string_list(w, &b->services);
    sc_json_byte(w, ',');
    kv_bool(w, "connected", &b->connected);
    sc_json_raw(w, ",");
    kv_i32(w, "heartRate", &b->heart_rate);
    sc_json_raw(w, ",");
    kv_str(w, "statusText", &b->status_text);
    sc_json_raw(w, ",");
    kv_i64(w, "lastSeenAt", &b->last_seen_at);
    sc_json_byte(w, '}');
}

static void render_nekotrak(sc_json_w_t *w, const sc_nekotrak_block_t *b)
{
    sc_json_raw(w, ",\"nekotrak\":{");
    kv_str(w, "version", &b->version);
    sc_json_raw(w, ",");
    kv_str(w, "source", &b->source);
    sc_json_raw(w, ",");
    kv_i64(w, "uptimeSec", &b->uptime_sec);
    sc_json_raw(w, ",");
    kv_i64(w, "startedAt", &b->started_at);
    sc_json_raw(w, ",");
    kv_i64(w, "lastLogAt", &b->last_log_at);
    sc_json_raw(w, ",");
    kv_bool(w, "trackerActive", &b->tracker_active);
    sc_json_raw(w, ",");
    kv_i64(w, "trackerStartedAt", &b->tracker_started_at);
    sc_json_raw(w, ",");
    kv_i64(w, "trackerStoppedAt", &b->tracker_stopped_at);
    sc_json_raw(w, ",");
    kv_str(w, "currentGame", &b->current_game);
    sc_json_raw(w, ",");
    kv_i32(w, "currentGameAppId", &b->current_game_app_id);
    sc_json_raw(w, ",\"pet\":");
    if (b->has_pet && b->pet_json[0] != '\0') {
        sc_json_raw(w, b->pet_json);
    } else {
        sc_json_null(w);
    }
    sc_json_raw(w, ",\"pomodoro\":");
    if (b->has_pomodoro && b->pomodoro_json[0] != '\0') {
        sc_json_raw(w, b->pomodoro_json);
    } else {
        sc_json_null(w);
    }
    sc_json_raw(w, ",\"warns\":");
    render_string_list(w, &b->warns);
    sc_json_byte(w, '}');
}

static void render_device(sc_json_w_t *w, const sc_device_t *d)
{
    sc_json_byte(w, '{');
    kv_str(w, "id", &d->id);
    sc_json_raw(w, ",");
    kv_str(w, "platform", &d->platform);
    sc_json_raw(w, ",");
    kv_str(w, "model", &d->model);
    sc_json_raw(w, ",");
    kv_str(w, "displayName", &d->display_name);
    sc_json_raw(w, ",");
    kv_i32(w, "batteryLevel", &d->battery_level);
    sc_json_raw(w, ",");
    kv_bool(w, "isCharging", &d->is_charging);
    sc_json_raw(w, ",");
    kv_str(w, "mediaTitle", &d->media_title);
    sc_json_raw(w, ",");
    kv_str(w, "mediaArtist", &d->media_artist);
    sc_json_raw(w, ",\"mediaState\":");
    if (d->media_state.has) {
        sc_json_str(w, d->media_state.v);
    } else {
        sc_json_str(w, "unknown");
    }
    sc_json_raw(w, ",");
    kv_str(w, "foregroundApp", &d->foreground_app);
    sc_json_raw(w, ",");
    kv_str(w, "foregroundAppName", &d->foreground_app_name);
    sc_json_raw(w, ",");
    kv_i64(w, "updatedAt", &d->updated_at);
    sc_json_raw(w, ",");
    kv_str(w, "error", &d->error);

    if (d->has_console) {
        render_console(w, &d->console);
    }
    if (d->has_node) {
        render_node(w, &d->node);
    }
    if (d->has_transport) {
        render_transport(w, &d->transport);
    }
    if (d->has_ble) {
        render_ble(w, &d->ble);
    }
    if (d->has_nekotrak) {
        render_nekotrak(w, &d->nekotrak);
    }
    sc_json_byte(w, '}');
}

static void render_into(sc_json_w_t *w, const sc_snapshot_t *snapshot)
{
    sc_json_raw(w, "{\"schema\":3,\"generatedAt\":");
    sc_json_int(w, snapshot->generated_at);
    sc_json_raw(w, ",\"devices\":[");
    for (int i = 0; i < snapshot->count; ++i) {
        if (i > 0) {
            sc_json_byte(w, ',');
        }
        render_device(w, &snapshot->devices[i]);
    }
    sc_json_raw(w, "]}");
}

size_t sc_snapshot_render(const sc_snapshot_t *snapshot, char *buf, size_t cap)
{
    if (buf == NULL || cap == 0) {
        return 0;
    }
    sc_json_w_t w;
    sc_json_w_init(&w, buf, cap);
    render_into(&w, snapshot);
    return w.oom ? 0 : w.len;
}

size_t sc_snapshot_render_len(const sc_snapshot_t *snapshot)
{
    sc_json_w_t w;
    sc_json_w_init(&w, NULL, 0); /* counting mode */
    render_into(&w, snapshot);
    return w.len;
}
