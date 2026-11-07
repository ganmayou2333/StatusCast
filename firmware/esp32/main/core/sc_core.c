#include "sc_core.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"

/* ---------------------------------------------------------------- strings */

void sc_str_clear(sc_str_t *s)
{
    s->has = false;
    s->v[0] = '\0';
}

/* --------------------------------------------------------------- UTF-8 */

/* Byte length of the coded character starting at src[i], bounded by end.
 * Malformed input is one opaque byte (mirrors .NET's replacement handling). */
static size_t utf8_seq_len(const char *src, size_t i, size_t end, uint32_t *cp)
{
    unsigned char b0 = (unsigned char)src[i];
    size_t need;
    uint32_t value;

    if (b0 < 0x80) {
        *cp = b0;
        return 1;
    }
    if ((b0 & 0xE0) == 0xC0) {
        need = 1;
        value = b0 & 0x1Fu;
    } else if ((b0 & 0xF0) == 0xE0) {
        need = 2;
        value = b0 & 0x0Fu;
    } else if ((b0 & 0xF8) == 0xF0) {
        need = 3;
        value = b0 & 0x07u;
    } else {
        *cp = 0xFFFD; /* stray continuation byte or invalid lead */
        return 1;
    }
    if (i + need >= end) {
        *cp = 0xFFFD;
        return 1;
    }
    for (size_t k = 1; k <= need; ++k) {
        unsigned char bk = (unsigned char)src[i + k];
        if ((bk & 0xC0) != 0x80) {
            *cp = 0xFFFD;
            return 1;
        }
        value = (value << 6) | (bk & 0x3Fu);
    }
    *cp = value;
    return need + 1;
}

/* .NET's char.IsWhiteSpace set, which is what string.Trim() uses. Matching it
 * matters because the daemon trims with .NET semantics: if this side keeps a
 * character the daemon drops, the same input renders differently in the two
 * implementations. */
static bool cp_is_space(uint32_t cp)
{
    if (cp == 0x20 || (cp >= 0x09 && cp <= 0x0D)) {
        return true;
    }
    if (cp == 0x85 || cp == 0xA0 || cp == 0x1680) {
        return true;
    }
    if (cp >= 0x2000 && cp <= 0x200A) {
        return true;
    }
    if (cp == 0x2028 || cp == 0x2029 || cp == 0x202F || cp == 0x205F) {
        return true;
    }
    return cp == 0x3000;
}

/* Copy src[from..to) into s, truncating on a UTF-8 character boundary.
 * Cutting by bytes alone would leave a partial sequence at the tail, which the
 * escaper then turns into one U+FFFD per leftover byte. */
static void store_span(sc_str_t *s, const char *src, size_t from, size_t to)
{
    size_t len = to - from;
    if (len == 0) {
        sc_str_clear(s);
        return;
    }
    if (len > SC_STR_LEN - 1) {
        size_t cut = SC_STR_LEN - 1;
        /* Back up while the first dropped byte is a continuation byte, so the
         * kept prefix ends on a character boundary. */
        while (cut > 0 && ((unsigned char)src[from + cut] & 0xC0) == 0x80) {
            --cut;
        }
        len = cut;
        if (len == 0) {
            sc_str_clear(s);
            return;
        }
    }
    memcpy(s->v, src + from, len);
    s->v[len] = '\0';
    s->has = true;
}

void sc_str_set(sc_str_t *s, const char *src)
{
    if (src == NULL) {
        sc_str_clear(s);
        return;
    }
    size_t end = strlen(src);
    size_t start = 0;
    size_t stop = 0;
    bool seen = false;
    size_t i = 0;

    /* Scan runes: remember the first and last non-whitespace rune, which trims
     * both ends while leaving interior whitespace alone. */
    while (i < end) {
        uint32_t cp = 0;
        size_t n = utf8_seq_len(src, i, end, &cp);
        if (!cp_is_space(cp)) {
            if (!seen) {
                start = i;
                seen = true;
            }
            stop = i + n;
        }
        i += n;
    }
    if (!seen) {
        sc_str_clear(s);
        return;
    }
    store_span(s, src, start, stop);
}

void sc_str_set_raw(sc_str_t *s, const char *src)
{
    if (src == NULL || src[0] == '\0') {
        sc_str_clear(s);
        return;
    }
    store_span(s, src, 0, strlen(src));
}

bool sc_str_is_blank(const char *s)
{
    if (s == NULL) {
        return true;
    }
    size_t end = strlen(s);
    size_t i = 0;
    while (i < end) {
        uint32_t cp = 0;
        i += utf8_seq_len(s, i, end, &cp);
        if (!cp_is_space(cp)) {
            return false;
        }
    }
    return true;
}

bool sc_str_eq(const char *a, const char *b)
{
    if (a == NULL || b == NULL) {
        return a == b;
    }
    return strcmp(a, b) == 0;
}

bool sc_str_eq_ci_ascii(const char *a, const char *b)
{
    if (a == NULL || b == NULL) {
        return a == b;
    }
    while (*a != '\0' && *b != '\0') {
        char ca = *a;
        char cb = *b;
        if (ca >= 'A' && ca <= 'Z') {
            ca = (char)(ca - 'A' + 'a');
        }
        if (cb >= 'A' && cb <= 'Z') {
            cb = (char)(cb - 'A' + 'a');
        }
        if (ca != cb) {
            return false;
        }
        ++a;
        ++b;
    }
    return *a == '\0' && *b == '\0';
}

void sc_strlist_clear(sc_strlist_t *list)
{
    list->count = 0;
    for (int i = 0; i < SC_MAX_LIST_ITEMS; ++i) {
        list->items[i][0] = '\0';
    }
}

bool sc_strlist_add(sc_strlist_t *list, const char *item)
{
    if (list->count >= SC_MAX_LIST_ITEMS || sc_str_is_blank(item)) {
        return false;
    }
    size_t len = strlen(item);
    while (len > 0 && (unsigned char)item[len - 1] <= ' ') {
        --len;
    }
    const char *start = item;
    while (*start != '\0' && (unsigned char)*start <= ' ') {
        ++start;
    }
    if (len > SC_LIST_ITEM_LEN - 1) {
        len = SC_LIST_ITEM_LEN - 1;
    }
    memcpy(list->items[list->count], start, len);
    list->items[list->count][len] = '\0';
    list->count += 1;
    return true;
}

/* --------------------------------------------------------------- platform */

static const char *const k_platform_tokens[] = {
    [SC_PLATFORM_IOS] = "ios",
    [SC_PLATFORM_ANDROID] = "android",
    [SC_PLATFORM_CONSOLE] = "console",
    [SC_PLATFORM_MACOS] = "macos",
    [SC_PLATFORM_LINUX] = "linux",
    [SC_PLATFORM_BLE] = "ble",
    [SC_PLATFORM_WINDOWS] = "windows",
    [SC_PLATFORM_UNKNOWN] = "unknown",
};

const char *sc_platform_token(sc_platform_t platform)
{
    if (platform < SC_PLATFORM_IOS || platform > SC_PLATFORM_UNKNOWN) {
        return "unknown";
    }
    return k_platform_tokens[platform];
}

sc_platform_t sc_platform_from_token(const char *token)
{
    if (token == NULL) {
        return SC_PLATFORM_UNKNOWN;
    }
    for (int i = 0; i <= (int)SC_PLATFORM_UNKNOWN; ++i) {
        if (k_platform_tokens[i] != NULL && strcmp(k_platform_tokens[i], token) == 0) {
            return (sc_platform_t)i;
        }
    }
    return SC_PLATFORM_UNKNOWN;
}

/* ------------------------------------------------------------ snapshot/row */

void sc_device_init(sc_device_t *device)
{
    memset(device, 0, sizeof(*device));
}

void sc_snapshot_init(sc_snapshot_t *snapshot)
{
    snapshot->generated_at = 0;
    snapshot->count = 0;
    for (int i = 0; i < SC_MAX_DEVICES; ++i) {
        sc_device_init(&snapshot->devices[i]);
    }
}

sc_device_t *sc_snapshot_append(sc_snapshot_t *snapshot)
{
    if (snapshot->count >= SC_MAX_DEVICES) {
        return NULL;
    }
    sc_device_t *device = &snapshot->devices[snapshot->count];
    sc_device_init(device);
    snapshot->count += 1;
    return device;
}

sc_device_t *sc_snapshot_find(sc_snapshot_t *snapshot, const char *id)
{
    if (id == NULL) {
        return NULL;
    }
    for (int i = 0; i < snapshot->count; ++i) {
        if (snapshot->devices[i].id.has && strcmp(snapshot->devices[i].id.v, id) == 0) {
            return &snapshot->devices[i];
        }
    }
    return NULL;
}

/* ---------------------------------------------------------------- aliases */

void sc_alias_table_init(sc_alias_table_t *table)
{
    table->count = 0;
    for (int i = 0; i < SC_MAX_ALIASES; ++i) {
        table->items[i].from[0] = '\0';
        table->items[i].to[0] = '\0';
    }
}

bool sc_alias_table_add(sc_alias_table_t *table, const char *from, const char *to)
{
    if (table->count >= SC_MAX_ALIASES || sc_str_is_blank(from) || sc_str_is_blank(to)) {
        return false;
    }
    size_t flen = strlen(from);
    size_t tlen = strlen(to);
    if (flen > SC_STR_LEN - 1) {
        flen = SC_STR_LEN - 1;
    }
    if (tlen > SC_STR_LEN - 1) {
        tlen = SC_STR_LEN - 1;
    }
    memcpy(table->items[table->count].from, from, flen);
    table->items[table->count].from[flen] = '\0';
    memcpy(table->items[table->count].to, to, tlen);
    table->items[table->count].to[tlen] = '\0';
    table->count += 1;
    return true;
}

const char *sc_display_name(const sc_alias_table_t *table, const char *model, char *out, size_t out_cap)
{
    if (out == NULL || out_cap == 0) {
        return "";
    }
    out[0] = '\0';

    if (!sc_str_is_blank(model)) {
        if (table != NULL) {
            for (int i = 0; i < table->count; ++i) {
                if (sc_str_eq_ci_ascii(table->items[i].from, model)) {
                    size_t len = strlen(table->items[i].to);
                    if (len > out_cap - 1) {
                        len = out_cap - 1;
                    }
                    memcpy(out, table->items[i].to, len);
                    out[len] = '\0';
                    return out;
                }
            }
        }

        /* Hard-coded iPhone display map (StatusService.ModelDisplay), exact match. */
        if (strcmp(model, "iPhone15,3") == 0) {
            (void)snprintf(out, out_cap, "%s", "14 Pro Max");
            return out;
        }
        if (strcmp(model, "iPhone14,3") == 0) {
            (void)snprintf(out, out_cap, "%s", "13 Pro Max");
            return out;
        }

        (void)snprintf(out, out_cap, "%s", model);
        return out;
    }

    (void)snprintf(out, out_cap, "%s", "未知机型");
    return out;
}

/* ---------------------------------------------------------------- routing */

sc_route_t sc_route_resolve(const char *method, const char *path)
{
    if (method == NULL || !sc_str_eq_ci_ascii(method, "GET")) {
        return SC_ROUTE_METHOD_NOT_ALLOWED;
    }
    if (path == NULL) {
        return SC_ROUTE_NOT_FOUND;
    }
    if (strcmp(path, "/health") == 0) {
        return SC_ROUTE_HEALTH;
    }
    if (strcmp(path, "/status/") == 0) {
        return SC_ROUTE_STATUS;
    }
    return SC_ROUTE_NOT_FOUND;
}

/* ----------------------------------------------------------------- health */

static bool json_get_i64(const cJSON *root, const char *name, int64_t *out)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (item == NULL || !cJSON_IsNumber(item)) {
        return false;
    }
    double raw = item->valuedouble;
    if (raw != (double)(int64_t)raw) {
        return false;
    }
    *out = (int64_t)raw;
    return true;
}

static bool json_get_i32(const cJSON *root, const char *name, int32_t *out)
{
    int64_t value = 0;
    if (!json_get_i64(root, name, &value)) {
        return false;
    }
    if (value < INT32_MIN || value > INT32_MAX) {
        return false;
    }
    *out = (int32_t)value;
    return true;
}

/* True when everything from `p` to `end` is JSON-insignificant whitespace. */
static bool only_whitespace_left(const char *p, const char *end)
{
    while (p < end) {
        char c = *p++;
        if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
            return false;
        }
    }
    return true;
}

void sc_health_evaluate_json(const char *json, size_t len, sc_health_t *out)
{
    memset(out, 0, sizeof(*out));
    if (json == NULL || len == 0) {
        return;
    }
    /* cJSON stops at the end of the first value and ignores trailing bytes; the
     * daemon parses with System.Text.Json, which rejects them. Read the parse
     * end position so both sides agree on what counts as a usable snapshot. */
    const char *parse_end = NULL;
    cJSON *root = cJSON_ParseWithLengthOpts(json, len, &parse_end, 0);
    if (root == NULL) {
        return;
    }
    if (!cJSON_IsObject(root) || parse_end == NULL ||
        !only_whitespace_left(parse_end, json + len)) {
        cJSON_Delete(root);
        return;
    }

    const cJSON *devices = cJSON_GetObjectItemCaseSensitive(root, "devices");
    if (devices == NULL || !cJSON_IsArray(devices)) {
        cJSON_Delete(root);
        return;
    }

    out->ok = true;
    out->has_device_count = true;
    out->device_count = (int32_t)cJSON_GetArraySize(devices);
    out->has_generated_at = json_get_i64(root, "generatedAt", &out->generated_at);
    out->has_schema = json_get_i32(root, "schema", &out->schema);
    cJSON_Delete(root);
}

/* ------------------------------------------------------------- hysteresis */

bool sc_hysteresis_apply(sc_probe_state_t *state, bool probe_ok, int64_t now)
{
    if (probe_ok) {
        state->failures = 0;
        state->online = true;
        state->has_last_seen = true;
        state->last_seen_at = now;
        return true;
    }

    if (state->failures < SC_FAILS_TO_OFFLINE) {
        state->failures += 1;
    }
    if (state->failures >= SC_FAILS_TO_OFFLINE) {
        state->online = false;
    }
    return state->online;
}
