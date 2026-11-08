#include "lan_collector.h"

#include "clock_util.h"

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "ping/ping_sock.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lwip/inet.h"
#include "lwip/sockets.h"
#include "lwip/sys.h"

#include "app_state.h"
#include "config_store.h"
#include "net/wifi_sta.h"

static const char *TAG = "sc_lan";

#define SC_PING_TIMEOUT_MS 800
#define SC_TCP_CONNECT_TIMEOUT_MS 300
#define SC_PING_MAX_COUNT 1

typedef enum {
    PROBE_METHOD_NONE = 0,
    PROBE_METHOD_PING,
    PROBE_METHOD_TCP,
} probe_method_t;

typedef struct {
    SemaphoreHandle_t done;
    bool replied;
} ping_ctx_t;

static void ping_success_cb(esp_ping_handle_t handle, void *args)
{
    (void)handle;
    ping_ctx_t *ctx = (ping_ctx_t *)args;
    if (ctx != NULL) {
        ctx->replied = true;
    }
}

static void ping_timeout_cb(esp_ping_handle_t handle, void *args)
{
    (void)handle;
    (void)args;
}

static void ping_end_cb(esp_ping_handle_t handle, void *args)
{
    (void)handle;
    ping_ctx_t *ctx = (ping_ctx_t *)args;
    if (ctx != NULL && ctx->done != NULL) {
        (void)xSemaphoreGive(ctx->done);
    }
}

static bool icmp_ping(const char *ip)
{
    ip_addr_t target;
    if (ipaddr_aton(ip, &target) == 0) {
        return false;
    }

    ping_ctx_t ctx = {
        .done = xSemaphoreCreateBinary(),
        .replied = false,
    };
    if (ctx.done == NULL) {
        return false;
    }

    esp_ping_config_t config = ESP_PING_DEFAULT_CONFIG();
    config.target_addr = target;
    config.count = SC_PING_MAX_COUNT;
    config.interval_ms = 100;
    config.timeout_ms = SC_PING_TIMEOUT_MS;
    config.data_size = 32;
    config.task_stack_size = 3072;

    esp_ping_callbacks_t callbacks = {
        .cb_args = &ctx,
        .on_ping_success = ping_success_cb,
        .on_ping_timeout = ping_timeout_cb,
        .on_ping_end = ping_end_cb,
    };

    esp_ping_handle_t ping = NULL;
    esp_err_t err = esp_ping_new_session(&config, &callbacks, &ping);
    if (err != ESP_OK) {
        vSemaphoreDelete(ctx.done);
        return false;
    }
    (void)esp_ping_start(ping);
    (void)xSemaphoreTake(ctx.done, pdMS_TO_TICKS(SC_PING_TIMEOUT_MS + 500));
    (void)esp_ping_delete_session(ping);
    vSemaphoreDelete(ctx.done);
    return ctx.replied;
}

static bool tcp_probe(const char *ip, uint16_t port)
{
    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock < 0) {
        return false;
    }

    int flags = fcntl(sock, F_GETFL, 0);
    (void)fcntl(sock, F_SETFL, flags | O_NONBLOCK);

    struct sockaddr_in address;
    memset(&address, 0, sizeof(address));
    address.sin_family = AF_INET;
    address.sin_port = htons(port);
    if (inet_aton(ip, &address.sin_addr) == 0) {
        close(sock);
        return false;
    }

    bool reachable = false;
    int rc = connect(sock, (struct sockaddr *)&address, sizeof(address));
    if (rc == 0) {
        reachable = true;
    } else if (errno == EINPROGRESS) {
        fd_set writable;
        fd_set errors;
        FD_ZERO(&writable);
        FD_ZERO(&errors);
        FD_SET(sock, &writable);
        FD_SET(sock, &errors);
        struct timeval timeout;
        timeout.tv_sec = 0;
        timeout.tv_usec = SC_TCP_CONNECT_TIMEOUT_MS * 1000;
        int ready = select(sock + 1, NULL, &writable, &errors, &timeout);
        if (ready > 0 && FD_ISSET(sock, &writable)) {
            int so_error = 0;
            socklen_t length = sizeof(so_error);
            if (getsockopt(sock, SOL_SOCKET, SO_ERROR, &so_error, &length) == 0 &&
                so_error == 0) {
                reachable = true;
            }
        }
    }

    close(sock);
    return reachable;
}

static probe_method_t probe_host(const sc_console_cfg_t *host, uint16_t *matched_port,
                                 int64_t deadline_ms)
{
    if (!wifi_is_connected()) {
        return PROBE_METHOD_NONE;
    }
    if (icmp_ping(host->ip.v)) {
        return PROBE_METHOD_PING;
    }
    for (int i = 0; i < host->tcp_port_count; ++i) {
        if (sc_deadline_reached(deadline_ms, sc_mono_ms())) {
            /* Out of budget mid-host: report what we know (nothing answered)
             * rather than half-probing. Hysteresis treats it like any other
             * failed round, so a persistently starved host stays offline. */
            ESP_LOGW(TAG, "lan %s: round budget spent; skipping remaining ports", host->id.v);
            return PROBE_METHOD_NONE;
        }
        if (tcp_probe(host->ip.v, host->tcp_ports[i])) {
            if (matched_port != NULL) {
                *matched_port = host->tcp_ports[i];
            }
            return PROBE_METHOD_TCP;
        }
    }
    return PROBE_METHOD_NONE;
}

int lan_collector_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now,
                      int64_t deadline_ms)
{
    if (cfg == NULL || snapshot == NULL) {
        return 0;
    }
    int appended = 0;

    for (int index = 0; index < cfg->console_count; ++index) {
        if (sc_deadline_reached(deadline_ms, sc_mono_ms())) {
            ESP_LOGW(TAG, "round budget spent; %d console(s) not probed this round",
                     cfg->console_count - index);
            break;
        }
        (void)sc_wdt_feed();
        const sc_console_cfg_t *host = &cfg->consoles[index];

        sc_persist_state_t state;
        (void)state_load_console(index, &state);
        if (!state.online && state.failures > 0) {
            state.online = false;
        }

        sc_probe_state_t probe = {
            .failures = state.failures,
            .online = state.online,
            .has_last_seen = state.has_last_seen,
            .last_seen_at = state.last_seen_at,
        };

        uint16_t matched_port = 0;
        probe_method_t method = probe_host(host, &matched_port, deadline_ms);
        bool ok = method != PROBE_METHOD_NONE;
        bool online = sc_hysteresis_apply(&probe, ok, now);

        sc_persist_state_t updated = state;
        updated.failures = probe.failures;
        updated.online = probe.online;
        updated.has_last_seen = probe.has_last_seen;
        updated.last_seen_at = probe.last_seen_at;
        (void)state_save_console(index, &updated);

        sc_device_t *device = sc_snapshot_append(snapshot);
        if (device == NULL) {
            break;
        }
        sc_str_set_raw(&device->id, host->id.v);
        sc_str_set_raw(&device->platform, "console");
        sc_str_set_raw(&device->model, host->name.v);
        char display[SC_STR_LEN];
        sc_str_set_raw(&device->display_name,
                       sc_display_name(&cfg->aliases, host->name.v, display, sizeof(display)));
        device->updated_at.has = true;
        device->updated_at.v = now;

        device->has_console = true;
        device->console.online.has = true;
        device->console.online.v = online;
        device->console.idle_hint.has = true;
        device->console.idle_hint.v = true;
        sc_str_set_raw(&device->console.name, host->name.v);
        sc_str_set_raw(&device->console.type, host->type.v);
        if (probe.has_last_seen) {
            device->console.last_seen_at.has = true;
            device->console.last_seen_at.v = probe.last_seen_at;
        }
        if (state.has_mac) {
            sc_str_set_raw(&device->console.mac, state.mac);
        }
        if (!online) {
            sc_str_set(&device->console.status_text, host->offline_text.v);
        }

        device->has_transport = true;
        if (method == PROBE_METHOD_PING) {
            sc_str_set_raw(&device->transport.active, "lan");
            sc_str_set_raw(&device->transport.detail, "ping");
            if (online) {
                sc_str_set_raw(&device->console.method, "ping");
            }
        } else if (method == PROBE_METHOD_TCP) {
            char detail[24];
            (void)snprintf(detail, sizeof(detail), "tcp:%u", (unsigned)matched_port);
            sc_str_set_raw(&device->transport.active, "tcp");
            sc_str_set_raw(&device->transport.detail, detail);
            if (online) {
                sc_str_set_raw(&device->console.method, "tcp");
            }
        } else {
            sc_str_set_raw(&device->transport.active, "none");
        }
        device->transport.degraded.has = true;
        device->transport.degraded.v = false;
        device->transport.online.has = true;
        device->transport.online.v = online;
        if (probe.has_last_seen) {
            device->transport.last_seen_at.has = true;
            device->transport.last_seen_at.v = probe.last_seen_at;
        }

        if (online && method == PROBE_METHOD_NONE) {
            /* Hysteresis says online but this round produced no probe; keep the
             * row honest by not inventing a method. */
            ESP_LOGD(TAG, "%s: hysteresis online without a probe", host->id.v);
        }

        appended += 1;
    }

    return appended;
}
