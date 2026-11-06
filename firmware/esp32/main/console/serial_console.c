#include "serial_console.h"

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "esp_console.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "app_state.h"
#include "collectors/ble_collector.h"
#include "config_store.h"
#include "mem_util.h"
#include "net/wifi_sta.h"

static const char *TAG = "sc_console";

#define CONSOLE_UART ((uart_port_t)CONFIG_SC_CONSOLE_UART_NUM)
#define CONSOLE_LINE_MAX 256
#define CONSOLE_JSON_MAX 8192

static bool s_running;

/* Command output goes straight to the UART instead of stdio: the console VFS is
 * not a tty, so stdio buffering made command output invisible. */
static void con_write(const char *format, ...)
{
    char buffer[256];
    va_list args;
    va_start(args, format);
    int length = vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    if (length > 0) {
        if (length > (int)sizeof(buffer) - 1) {
            length = (int)sizeof(buffer) - 1;
        }
        uart_write_bytes(CONSOLE_UART, buffer, (size_t)length);
    }
}

/* ------------------------------------------------------------- line reader */

/* Reads one line from the console UART, echoing as it goes. Returns false on
 * timeout, true when a full line (without CR/LF) is available. */
static bool read_line(char *buffer, size_t cap, TickType_t timeout)
{
    size_t used = 0;
    while (used + 1 < cap) {
        uint8_t ch = 0;
        int read = uart_read_bytes(CONSOLE_UART, &ch, 1, timeout);
        if (read <= 0) {
            return false;
        }
        if (ch == '\r' || ch == '\n') {
            uart_write_bytes(CONSOLE_UART, "\r\n", 2);
            break;
        }
        if (ch == 0x08 || ch == 0x7F) {
            if (used > 0) {
                used -= 1;
                uart_write_bytes(CONSOLE_UART, "\b \b", 3);
            }
            continue;
        }
        buffer[used++] = (char)ch;
        uart_write_bytes(CONSOLE_UART, (const char *)&ch, 1);
    }
    buffer[used] = '\0';
    return true;
}

/* --------------------------------------------------------------- commands */

static int cmd_help(int argc, char **argv);
static int cmd_cfg(int argc, char **argv);
static int cmd_net(int argc, char **argv);
static int cmd_heap(int argc, char **argv);
/* Lists nearby BLE peripherals so a smart name/address can be put into
 * config.json without guesswork. Takes one 4 s scan. */
static int cmd_ble(int argc, char **argv)
{
    if (argc < 2 || strcmp(argv[1], "scan") != 0) {
        con_write("usage: ble scan\r\n");
        return 1;
    }
    static char names[16][32];
    static char addrs[16][18];
    static int8_t rssi[16];

    con_write("scanning 4 s ...\r\n");
    int count = ble_collector_scan_list(16, names, addrs, rssi);
    if (count <= 0) {
        con_write("no devices found (or a collection round holds the BLE lock)\r\n");
        return 1;
    }
    con_write("%2s  %-17s %5s  %s\r\n", "#", "address", "rssi", "name");
    for (int i = 0; i < count; ++i) {
        con_write("%2d  %-17s %5d  %s\r\n", i, addrs[i], (int)rssi[i], names[i]);
    }
    return 0;
}

/* Dumps the exact bytes /status/ would serve. Lets the wire contract be checked
 * over the serial console before WiFi is provisioned. */
static int cmd_json(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    static char *buffer;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(CONFIG_SC_MAX_SNAPSHOT_JSON);
        if (buffer == NULL) {
            con_write("no memory\r\n");
            return 1;
        }
    }
    size_t length = app_state_copy(buffer, CONFIG_SC_MAX_SNAPSHOT_JSON);
    if (length == 0) {
        con_write("snapshot not ready\r\n");
        return 1;
    }
    uart_write_bytes(CONSOLE_UART, buffer, length);
    con_write("\r\n");
    return 0;
}

static int cmd_reboot(int argc, char **argv);
static int cmd_stats(int argc, char **argv);
static int cmd_json(int argc, char **argv);
static int cmd_ble(int argc, char **argv);

static const esp_console_cmd_t s_commands[] = {
    {.command = "help", .help = "list commands", .func = cmd_help, .argtable = NULL},
    {.command = "cfg", .help = "cfg show | cfg put | cfg reset", .func = cmd_cfg, .argtable = NULL},
    {.command = "net", .help = "net ssid <ssid> | net pass <pw> | net status", .func = cmd_net,
     .argtable = NULL},
    {.command = "heap", .help = "show free heap", .func = cmd_heap, .argtable = NULL},
    {.command = "stats", .help = "show runtime counters", .func = cmd_stats, .argtable = NULL},
    {.command = "json", .help = "dump the current /status/ body", .func = cmd_json,
     .argtable = NULL},
    {.command = "ble", .help = "ble scan - list nearby BLE peripherals", .func = cmd_ble,
     .argtable = NULL},
    {.command = "reboot", .help = "restart the device", .func = cmd_reboot, .argtable = NULL},
};

static int cmd_help(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    for (size_t i = 0; i < sizeof(s_commands) / sizeof(s_commands[0]); ++i) {
        con_write("  %-8s %s\r\n", s_commands[i].command, s_commands[i].help);
    }
    return 0;
}

static void print_config_summary(void)
{
    const sc_config_t *cfg = app_config();
    const app_network_t network = app_state_network();
    con_write("refresh    : %d s\r\n", cfg->refresh_seconds);
    con_write("aliases    : %d\r\n", cfg->aliases.count);
    con_write("consoles   : %d\r\n", cfg->console_count);
    for (int i = 0; i < cfg->console_count; ++i) {
        const sc_console_cfg_t *host = &cfg->consoles[i];
        con_write("  [%d] %-12s %-15s type=%-8s ports=%d offline=\"%s\"\r\n", i,
               host->id.has ? host->id.v : "?", host->ip.has ? host->ip.v : "-",
               host->type.has ? host->type.v : "-", host->tcp_port_count,
               host->offline_text.has ? host->offline_text.v : "");
    }
    con_write("ble devices: %d\r\n", cfg->ble_count);
    for (int i = 0; i < cfg->ble_count; ++i) {
        const sc_ble_cfg_t *ble = &cfg->ble[i];
        con_write("  [%d] %-12s addr=%-17s filter=%-12s svc=%s%s\r\n", i,
               ble->id.has ? ble->id.v : "?", ble->address.has ? ble->address.v : "-",
               ble->name_filter.has ? ble->name_filter.v : "-",
               ble->want_battery ? "battery " : "",
               ble->want_heart_rate ? "heart_rate" : "");
    }
    con_write("pc feed    : %s %s timeout=%ds\r\n", cfg->pc_enabled ? "on " : "off",
           cfg->pc_url.has ? cfg->pc_url.v : "-", cfg->pc_timeout_seconds);
    con_write("network    : %s %s rssi=%d host=%s\r\n", network.wifi_connected ? "up" : "down",
           network.ip[0] ? network.ip : "-", network.rssi, network.hostname);
}

static int cfg_put_paste(void)
{
    static char *buffer;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(CONSOLE_JSON_MAX);
        if (buffer == NULL) {
            con_write("no memory for the paste buffer\r\n");
            return 1;
        }
    }
    size_t used = 0;
    buffer[0] = '\0';
    con_write("paste config.json, finish with a line containing EOF\r\n");
    for (;;) {
        char line[CONSOLE_LINE_MAX];
        if (!read_line(line, sizeof(line), portMAX_DELAY)) {
            continue;
        }
        if (strcmp(line, "EOF") == 0) {
            break;
        }
        size_t length = strlen(line);
        if (used + length + 2 >= CONSOLE_JSON_MAX) {
            con_write("too large, aborted\r\n");
            return 1;
        }
        memcpy(buffer + used, line, length);
        used += length;
        buffer[used++] = '\n';
        buffer[used] = '\0';
    }
    if (used == 0) {
        con_write("empty input, nothing written\r\n");
        return 1;
    }
    if (!app_config_save_json(buffer, used)) {
        con_write("write failed\r\n");
        return 1;
    }
    con_write("config saved (%u bytes)\r\n", (unsigned)used);
    return 0;
}

static int cmd_cfg(int argc, char **argv)
{
    if (argc < 2) {
        con_write("usage: cfg show | cfg put | cfg reset\r\n");
        return 1;
    }
    if (strcmp(argv[1], "show") == 0) {
        print_config_summary();
        return 0;
    }
    if (strcmp(argv[1], "put") == 0) {
        return cfg_put_paste();
    }
    if (strcmp(argv[1], "reset") == 0) {
        if (!config_store_reset()) {
            con_write("reset failed\r\n");
            return 1;
        }
        (void)app_config_reload();
        con_write("config reset to compile-time default\r\n");
        return 0;
    }
    con_write("unknown subcommand\r\n");
    return 1;
}

static int cmd_net(int argc, char **argv)
{
    if (argc < 2) {
        con_write("usage: net ssid <ssid> | net pass <password> | net status\r\n");
        return 1;
    }
    if (strcmp(argv[1], "status") == 0) {
        const app_network_t network = app_state_network();
        con_write("wifi %s ip=%s rssi=%d\r\n", network.wifi_connected ? "connected" : "down",
               network.ip[0] ? network.ip : "-", network.rssi);
        return 0;
    }
    if (strcmp(argv[1], "ssid") == 0 || strcmp(argv[1], "pass") == 0) {
        if (argc < 3) {
            con_write("missing argument\r\n");
            return 1;
        }
        static char ssid[64];
        static char password[64];
        if (!wifi_creds_load(ssid, sizeof(ssid), password, sizeof(password))) {
            ssid[0] = '\0';
            password[0] = '\0';
        }
        if (strcmp(argv[1], "ssid") == 0) {
            (void)snprintf(ssid, sizeof(ssid), "%s", argv[2]);
        } else {
            (void)snprintf(password, sizeof(password), "%s", argv[2]);
        }
        if (!wifi_creds_save(ssid, password)) {
            con_write("NVS write failed\r\n");
            return 1;
        }
        con_write("%s updated, reconnecting\r\n", argv[1]);
        (void)wifi_reconnect(ssid, password);
        return 0;
    }
    con_write("unknown subcommand\r\n");
    return 1;
}

static int cmd_heap(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    con_write("free heap   : %u\r\n", (unsigned)esp_get_free_heap_size());
    con_write("min free    : %u\r\n", (unsigned)esp_get_minimum_free_heap_size());
    con_write("largest blk : %u\r\n", (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DEFAULT));
    con_write("psram free  : %u\r\n",
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    return 0;
}

static int cmd_stats(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    const app_network_t network = app_state_network();
    con_write("devices     : %d\r\n", app_state_device_count());
    con_write("last round  : %u ms\r\n", (unsigned)app_state_last_round_millis());
    con_write("snapshot    : %s\r\n", app_state_ready() ? "ready" : "not ready");
    con_write("generatedAt : %lld\r\n", (long long)app_state_generated_at());
    con_write("time valid  : %s\r\n", app_time_valid() ? "yes" : "no");
    con_write("ip          : %s\r\n", network.ip[0] ? network.ip : "-");
    return 0;
}

static int cmd_reboot(int argc, char **argv)
{
    (void)argc;
    (void)argv;
    con_write("rebooting\r\n");
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_restart();
    return 0;
}

/* ------------------------------------------------------------------- task */

static void console_task(void *arg)
{
    (void)arg;
    char line[CONSOLE_LINE_MAX];

    for (;;) {
        if (!read_line(line, sizeof(line), pdMS_TO_TICKS(1000))) {
            continue;
        }
        if (line[0] == '\0') {
            continue;
        }
        int result = 0;
        ESP_LOGI(TAG, "console line: \"%s\"", line);
        esp_err_t err = esp_console_run(line, &result);
        ESP_LOGI(TAG, "dispatch %s -> ret=%d", esp_err_to_name(err), result);
        if (err == ESP_ERR_NOT_FOUND) {
            con_write("unknown command: %s\r\n", line);
        } else if (err != ESP_OK) {
            con_write("error: %s\r\n", esp_err_to_name(err));
        }
    }
}

void serial_console_start(void)
{
    if (s_running) {
        return;
    }

    /* The console VFS is not a tty, so stdio defaults to full buffering and
     * command output would sit in the buffer until it fills up. */
    setvbuf(stdout, NULL, _IONBF, 0);
    setvbuf(stdin, NULL, _IONBF, 0);

    esp_console_config_t console_config = ESP_CONSOLE_CONFIG_DEFAULT();
    console_config.max_cmdline_length = CONSOLE_LINE_MAX;
    esp_err_t err = esp_console_init(&console_config);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "esp_console_init: %s", esp_err_to_name(err));
    }

    for (size_t i = 0; i < sizeof(s_commands) / sizeof(s_commands[0]); ++i) {
        if (esp_console_cmd_register(&s_commands[i]) != ESP_OK) {
            ESP_LOGW(TAG, "failed to register command %s", s_commands[i].command);
        }
    }

    const uart_config_t uart_config = {
        .baud_rate = 115200,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    err = uart_driver_install(CONSOLE_UART, 1024, 1024, 0, NULL, 0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "uart_driver_install: %s", esp_err_to_name(err));
    }
    (void)uart_param_config(CONSOLE_UART, &uart_config);
    uart_vfs_dev_use_driver(CONSOLE_UART);

    xTaskCreate(console_task, "sc_console", 4096, NULL, 4, NULL);
    s_running = true;
    con_write("\r\nStatusCast console ready, type `help`\r\n");
}
