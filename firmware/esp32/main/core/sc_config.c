/*
 * sc_config -- parse the firmware-side config.json (a PC-compatible subset).
 *
 * Parsing policy mirrors the Python/C# split described in the spec:
 *   - unreadable / non-object JSON keeps every default and never fails a round;
 *   - a single broken entry becomes a schema-3 error row (returned via `errors`),
 *     it does not hide the other devices.
 */
#include "sc_core.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"

static const char *json_str(const cJSON *object, const char *key)
{
    if (object == NULL) {
        return NULL;
    }
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (item == NULL || !cJSON_IsString(item)) {
        return NULL;
    }
    return item->valuestring;
}

static bool json_bool(const cJSON *object, const char *key, bool fallback)
{
    if (object == NULL) {
        return fallback;
    }
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (item == NULL || !cJSON_IsBool(item)) {
        return fallback;
    }
    return cJSON_IsTrue(item) != 0;
}

static bool json_int(const cJSON *object, const char *key, int *out)
{
    if (object == NULL) {
        return false;
    }
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (item == NULL || !cJSON_IsNumber(item)) {
        return false;
    }
    double raw = item->valuedouble;
    if (raw != (double)(int)raw) {
        return false;
    }
    *out = (int)raw;
    return true;
}

static bool valid_ipv4(const char *text)
{
    if (text == NULL) {
        return false;
    }
    int octets = 0;
    const char *p = text;
    while (*p != '\0') {
        if (octets >= 4) {
            return false;
        }
        int value = 0;
        int digits = 0;
        while (*p >= '0' && *p <= '9') {
            value = value * 10 + (*p - '0');
            if (value > 255) {
                return false;
            }
            ++p;
            ++digits;
        }
        if (digits == 0) {
            return false;
        }
        octets += 1;
        if (*p == '.') {
            ++p;
            if (*p == '\0') {
                return false;
            }
        } else if (*p != '\0') {
            return false;
        }
    }
    return octets == 4;
}

static int add_error(sc_config_error_t *errors, int max_errors, int count,
                     const char *platform, const char *id, const char *message)
{
    if (errors == NULL || count >= max_errors) {
        return count;
    }
    sc_str_set_raw(&errors[count].platform, platform);
    sc_str_set_raw(&errors[count].id, id);
    sc_str_set(&errors[count].message, message);
    return count + 1;
}

void sc_config_defaults(sc_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    cfg->refresh_seconds = SC_DEFAULT_REFRESH_SECONDS;
    sc_alias_table_init(&cfg->aliases);
    cfg->console_count = 0;
    cfg->ble_count = 0;
    cfg->pc_enabled = false;
    cfg->pc_timeout_seconds = SC_DEFAULT_PC_TIMEOUT_SECONDS;
    sc_str_set_raw(&cfg->pc_url, "http://127.0.0.1:5099/status/");
}

static void parse_tcp_ports(const cJSON *entry, sc_console_cfg_t *cfg)
{
    static const int defaults[4] = {9295, 3074, 80, 443};
    cfg->tcp_port_count = 0;
    const cJSON *list = cJSON_GetObjectItemCaseSensitive(entry, "tcpPorts");
    if (list != NULL && cJSON_IsArray(list)) {
        const cJSON *item = NULL;
        cJSON_ArrayForEach(item, list)
        {
            if (!cJSON_IsNumber(item)) {
                continue;
            }
            int value = (int)item->valuedouble;
            if (value < 1 || value > 65535) {
                continue;
            }
            bool duplicate = false;
            for (int i = 0; i < cfg->tcp_port_count; ++i) {
                if (cfg->tcp_ports[i] == (uint16_t)value) {
                    duplicate = true;
                }
            }
            if (duplicate || cfg->tcp_port_count >= SC_MAX_TCP_PORTS) {
                continue;
            }
            cfg->tcp_ports[cfg->tcp_port_count++] = (uint16_t)value;
        }
        return;
    }
    for (int i = 0; i < 4; ++i) {
        cfg->tcp_ports[i] = (uint16_t)defaults[i];
    }
    cfg->tcp_port_count = 4;
}

static void parse_consoles(const cJSON *root, sc_config_t *cfg,
                           sc_config_error_t *errors, int max_errors, int *error_count)
{
    const cJSON *consoles = cJSON_GetObjectItemCaseSensitive(root, "Consoles");
    if (consoles == NULL || !cJSON_IsArray(consoles)) {
        return;
    }
    const cJSON *entry = NULL;
    cJSON_ArrayForEach(entry, consoles)
    {
        if (!cJSON_IsObject(entry)) {
            *error_count = add_error(errors, max_errors, *error_count, "console", "Consoles", "配置项必须是对象");
            continue;
        }
        const char *id = json_str(entry, "id");
        const char *ip = json_str(entry, "ip");
        if (sc_str_is_blank(id) || sc_str_is_blank(ip)) {
            *error_count = add_error(errors, max_errors, *error_count, "console", "device", "主机配置缺少 id 或 ip");
            continue;
        }
        if (!valid_ipv4(ip)) {
            char message[64];
            (void)snprintf(message, sizeof(message), "主机 IP 非法：%s", ip);
            *error_count = add_error(errors, max_errors, *error_count, "console", id, message);
            continue;
        }
        if (cfg->console_count >= SC_MAX_CONSOLES) {
            *error_count = add_error(errors, max_errors, *error_count, "console", id, "主机数量超出上限");
            continue;
        }

        sc_console_cfg_t *target = &cfg->consoles[cfg->console_count];
        memset(target, 0, sizeof(*target));
        sc_str_set_raw(&target->id, id);
        const char *name = json_str(entry, "name");
        sc_str_set_raw(&target->name, sc_str_is_blank(name) ? id : name);
        const char *type = json_str(entry, "type");
        sc_str_set_raw(&target->type, sc_str_is_blank(type) ? "console" : type);
        sc_str_set_raw(&target->ip, ip);
        const char *offline = json_str(entry, "offlineText");
        sc_str_set(&target->offline_text, sc_str_is_blank(offline) ? SC_DEFAULT_OFFLINE_TEXT : offline);
        target->fallback = json_bool(entry, "fallback", true);
        parse_tcp_ports(entry, target);
        cfg->console_count += 1;
    }
}

static void parse_ble_devices(const cJSON *root, sc_config_t *cfg,
                              sc_config_error_t *errors, int max_errors, int *error_count)
{
    const cJSON *devices = cJSON_GetObjectItemCaseSensitive(root, "BleDevices");
    if (devices == NULL || !cJSON_IsArray(devices)) {
        return;
    }
    const cJSON *entry = NULL;
    cJSON_ArrayForEach(entry, devices)
    {
        if (!cJSON_IsObject(entry)) {
            *error_count = add_error(errors, max_errors, *error_count, "ble", "BleDevices", "配置项必须是对象");
            continue;
        }
        const char *id = json_str(entry, "id");
        if (sc_str_is_blank(id)) {
            *error_count = add_error(errors, max_errors, *error_count, "ble", "ble", "BLE 配置缺少 id");
            continue;
        }
        const char *address = json_str(entry, "address");
        const char *name_filter = json_str(entry, "nameFilter");
        if (sc_str_is_blank(address) && sc_str_is_blank(name_filter)) {
            *error_count = add_error(errors, max_errors, *error_count, "ble", id, "BLE 配置缺少 address 或 nameFilter");
            continue;
        }
        if (cfg->ble_count >= SC_MAX_BLE_DEVICES) {
            *error_count = add_error(errors, max_errors, *error_count, "ble", id, "BLE 数量超出上限");
            continue;
        }

        sc_ble_cfg_t *target = &cfg->ble[cfg->ble_count];
        memset(target, 0, sizeof(*target));
        sc_str_set_raw(&target->id, id);
        const char *name = json_str(entry, "name");
        sc_str_set_raw(&target->name, sc_str_is_blank(name) ? id : name);
        if (!sc_str_is_blank(address)) {
            sc_str_set_raw(&target->address, address);
        }
        if (!sc_str_is_blank(name_filter)) {
            sc_str_set_raw(&target->name_filter, name_filter);
        }

        bool any_service = false;
        const cJSON *services = cJSON_GetObjectItemCaseSensitive(entry, "services");
        if (services != NULL && cJSON_IsArray(services)) {
            const cJSON *service = NULL;
            cJSON_ArrayForEach(service, services)
            {
                if (!cJSON_IsString(service)) {
                    continue;
                }
                if (strcmp(service->valuestring, "battery") == 0) {
                    target->want_battery = true;
                    any_service = true;
                } else if (strcmp(service->valuestring, "heart_rate") == 0) {
                    target->want_heart_rate = true;
                    any_service = true;
                }
            }
        }
        if (!any_service) {
            target->want_battery = true;
        }

        target->heart_rate_wait_seconds = SC_DEFAULT_HR_WAIT_SECONDS;
        int wait_seconds = 0;
        if (json_int(entry, "heartRateWaitSeconds", &wait_seconds) && wait_seconds >= 1 &&
            wait_seconds <= 8) {
            target->heart_rate_wait_seconds = wait_seconds;
        }
        cfg->ble_count += 1;
    }
}

static void parse_pc_feed(const cJSON *root, sc_config_t *cfg)
{
    const cJSON *feed = cJSON_GetObjectItemCaseSensitive(root, "PcFeed");
    if (feed == NULL || !cJSON_IsObject(feed)) {
        return;
    }
    cfg->pc_enabled = json_bool(feed, "enabled", cfg->pc_enabled);
    const char *url = json_str(feed, "url");
    if (!sc_str_is_blank(url)) {
        sc_str_set_raw(&cfg->pc_url, url);
    }
    int timeout = 0;
    if (json_int(feed, "timeoutSeconds", &timeout) && timeout >= 1 && timeout <= 30) {
        cfg->pc_timeout_seconds = timeout;
    }
}

int sc_config_parse(const char *json, size_t len, sc_config_t *cfg,
                    sc_config_error_t *errors, int max_errors)
{
    sc_config_defaults(cfg);
    if (json == NULL || len == 0) {
        return 0;
    }
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (root == NULL) {
        return 0;
    }
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return 0;
    }

    int refresh = 0;
    if (json_int(root, "RefreshIntervalSeconds", &refresh) && refresh >= 5 && refresh <= 3600) {
        cfg->refresh_seconds = refresh;
    }

    const cJSON *aliases = cJSON_GetObjectItemCaseSensitive(root, "Aliases");
    if (aliases != NULL && cJSON_IsObject(aliases)) {
        const cJSON *alias = NULL;
        cJSON_ArrayForEach(alias, aliases)
        {
            if (!cJSON_IsString(alias) || alias->string == NULL) {
                continue;
            }
            (void)sc_alias_table_add(&cfg->aliases, alias->string, alias->valuestring);
        }
    }

    int error_count = 0;
    parse_consoles(root, cfg, errors, max_errors, &error_count);
    parse_ble_devices(root, cfg, errors, max_errors, &error_count);
    parse_pc_feed(root, cfg);

    cJSON_Delete(root);
    return error_count;
}
