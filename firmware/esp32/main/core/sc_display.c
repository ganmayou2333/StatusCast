#include "sc_display.h"

#include <stdio.h>
#include <string.h>

static char icon_for_platform(const char *platform)
{
    if (platform == NULL) {
        return '?';
    }
    if (strcmp(platform, "console") == 0) {
        return 'G';
    }
    if (strcmp(platform, "ios") == 0) {
        return 'P';
    }
    if (strcmp(platform, "android") == 0) {
        return 'A';
    }
    if (strcmp(platform, "ble") == 0) {
        return 'B';
    }
    if (strcmp(platform, "windows") == 0) {
        return 'W';
    }
    return '?';
}

void sc_display_ascii(const char *src, char *dst, size_t cap)
{
    if (dst == NULL || cap == 0) {
        return;
    }
    dst[0] = '\0';
    if (src == NULL) {
        return;
    }
    size_t out = 0;
    const unsigned char *p = (const unsigned char *)src;
    while (*p != 0 && out + 1 < cap) {
        unsigned char c = *p;
        if (c < 0x80) {
            if (c >= 0x20 && c < 0x7F) {
                dst[out++] = (char)c;
            } else if (c == '\t' || c == '\n' || c == '\r') {
                dst[out++] = ' ';
            }
            p += 1;
            continue;
        }
        /* Skip the whole UTF-8 sequence and emit a single placeholder so a
         * Chinese name does not turn into a wall of '?'. */
        int extra = 0;
        if ((c & 0xE0) == 0xC0) {
            extra = 1;
        } else if ((c & 0xF0) == 0xE0) {
            extra = 2;
        } else if ((c & 0xF8) == 0xF0) {
            extra = 3;
        }
        p += 1 + extra;
        dst[out++] = '?';
    }
    dst[out] = '\0';
}

static void set_text(char *dst, size_t cap, const char *src)
{
    sc_display_ascii(src, dst, cap);
}

static void format_battery(const sc_device_t *device, char *out, size_t cap)
{
    out[0] = '\0';
    if (device->battery_level.has) {
        const char *charging = "";
        if (device->is_charging.has && device->is_charging.v) {
            charging = "+";
        }
        (void)snprintf(out, cap, "%d%%%s", (int)device->battery_level.v, charging);
    } else {
        (void)snprintf(out, cap, "--");
    }
}

static void build_console_row(const sc_device_t *device, sc_display_row_t *row)
{
    row->icon = 'G';
    const char *name = device->display_name.has ? device->display_name.v
                       : device->model.has  ? device->model.v
                                            : NULL;
    set_text(row->title, sizeof(row->title), name != NULL ? name : "console");

    bool online = device->has_console && device->console.online.has && device->console.online.v;
    row->online = online;
    if (online) {
        const char *method = (device->has_console && device->console.method.has)
                                 ? device->console.method.v
                                 : "online";
        (void)snprintf(row->status, sizeof(row->status), "online %.12s", method);
    } else {
        (void)snprintf(row->status, sizeof(row->status), "offline");
    }
    if (device->error.has) {
        set_text(row->detail, sizeof(row->detail), device->error.v);
    } else if (device->has_console && device->console.mac.has) {
        set_text(row->detail, sizeof(row->detail), device->console.mac.v);
    } else {
        row->detail[0] = '\0';
    }
}

static void build_ble_row(const sc_device_t *device, sc_display_row_t *row)
{
    row->icon = 'B';
    const char *name = device->display_name.has ? device->display_name.v
                       : device->model.has  ? device->model.v
                                            : NULL;
    set_text(row->title, sizeof(row->title), name != NULL ? name : "ble");

    const sc_ble_block_t *ble = &device->ble;
    bool connected = device->has_ble && ble->connected.has && ble->connected.v;
    row->online = connected;
    row->degraded = device->has_transport && device->transport.degraded.has &&
                    device->transport.degraded.v;

    char battery[12];
    format_battery(device, battery, sizeof(battery));
    if (connected) {
        if (ble->heart_rate.has) {
            (void)snprintf(row->status, sizeof(row->status), "%s hr%d", battery,
                           (int)ble->heart_rate.v);
        } else {
            (void)snprintf(row->status, sizeof(row->status), "%s", battery);
        }
        set_text(row->detail, sizeof(row->detail), ble->address.has ? ble->address.v : "");
    } else if (device->has_ble) {
        (void)snprintf(row->status, sizeof(row->status), "not connected");
        row->detail[0] = '\0';
    } else {
        (void)snprintf(row->status, sizeof(row->status), "not found");
        row->detail[0] = '\0';
    }
}

static void build_phone_row(const sc_device_t *device, sc_display_row_t *row)
{
    row->icon = (device->platform.has && strcmp(device->platform.v, "android") == 0) ? 'A' : 'P';
    const char *name = device->display_name.has ? device->display_name.v
                       : device->model.has  ? device->model.v
                                            : NULL;
    set_text(row->title, sizeof(row->title), name != NULL ? name : "phone");

    format_battery(device, row->status, sizeof(row->status));
    row->online = device->error.has ? false : true;
    row->degraded = device->has_transport && device->transport.degraded.has &&
                    device->transport.degraded.v;

    if (device->error.has) {
        set_text(row->detail, sizeof(row->detail), device->error.v);
    } else if (device->media_title.has) {
        set_text(row->detail, sizeof(row->detail), device->media_title.v);
    } else {
        row->detail[0] = '\0';
    }
}

static void build_windows_row(const sc_device_t *device, sc_display_row_t *row)
{
    row->icon = 'W';
    const char *name = device->display_name.has ? device->display_name.v
                       : device->model.has  ? device->model.v
                                            : NULL;
    set_text(row->title, sizeof(row->title), name != NULL ? name : "windows");

    if (device->has_nekotrak && device->nekotrak.current_game.has) {
        (void)snprintf(row->status, sizeof(row->status), "playing");
        set_text(row->detail, sizeof(row->detail), device->nekotrak.current_game.v);
        row->online = true;
    } else {
        (void)snprintf(row->status, sizeof(row->status), "idle");
        row->detail[0] = '\0';
        row->online = device->error.has ? false : true;
    }
}

void sc_display_build(const sc_snapshot_t *snapshot, sc_display_model_t *out)
{
    memset(out, 0, sizeof(*out));
    if (snapshot == NULL) {
        return;
    }
    out->device_count = snapshot->count;
    for (int i = 0; i < snapshot->count && out->row_count < SC_DISPLAY_ROWS; ++i) {
        const sc_device_t *device = &snapshot->devices[i];
        sc_display_row_t *row = &out->rows[out->row_count];
        memset(row, 0, sizeof(*row));

        const char *platform = device->platform.has ? device->platform.v : "";
        if (strcmp(platform, "console") == 0) {
            build_console_row(device, row);
        } else if (strcmp(platform, "ble") == 0) {
            build_ble_row(device, row);
        } else if (strcmp(platform, "windows") == 0) {
            build_windows_row(device, row);
        } else {
            build_phone_row(device, row);
            row->icon = icon_for_platform(platform);
        }
        out->row_count += 1;
    }
}

void sc_display_summary(const sc_display_model_t *model, char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return;
    }
    int online = 0;
    for (int i = 0; i < model->row_count; ++i) {
        if (model->rows[i].online) {
            online += 1;
        }
    }
    (void)snprintf(out, cap, "%d dev %d on", model->device_count, online);
}
