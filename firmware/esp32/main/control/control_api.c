#include "control_api.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "esp_core_dump.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "lwip/sockets.h"
#include "mbedtls/sha256.h"

#include "app_state.h"
#include "audit.h"
#include "collectors/ble_collector.h"
#include "config_guard.h"
#include "config_store.h"
#include "sc_route_counts.h"
#include "log_ring.h"
#include "mem_util.h"
#include "net/wifi_sta.h"
#include "sc_core.h"
#include "ui/ssd1315.h"
#include "ui/status_led.h"

static const char *TAG = "sc_control";

#define CONTROL_TOKEN_HEADER "X-StatusCast-Token"
#define CONTROL_BODY_MAX 8192

static const char BODY_UNAUTHORIZED[] = "{\"error\":\"unauthorized\"}";
static const char BODY_NO_DUMP[] = "{\"error\":\"no coredump\"}";
static const char BODY_BAD_REQUEST[] = "{\"error\":\"bad request\"}";

/* --------------------------------------------------------------- helpers */

static esp_err_t send_body(httpd_req_t *req, const char *status, const char *content_type,
                           const char *body, size_t length)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, content_type);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, body, (ssize_t)length);
}

static esp_err_t send_json(httpd_req_t *req, const char *status, const char *body)
{
    return send_body(req, status, "application/json; charset=utf-8", body, strlen(body));
}

static void peer_ip(httpd_req_t *req, char *out, size_t cap)
{
    if (out == NULL || cap == 0) {
        return;
    }
    out[0] = '\0';
    struct sockaddr_storage storage;
    socklen_t length = sizeof(storage);
    const int fd = httpd_req_to_sockfd(req);
    if (fd < 0 || getpeername(fd, (struct sockaddr *)&storage, &length) != 0) {
        return;
    }
    if (storage.ss_family == AF_INET) {
        const struct sockaddr_in *address = (const struct sockaddr_in *)&storage;
        (void)inet_ntoa_r(address->sin_addr, out, cap);
    } else if (storage.ss_family == AF_INET6) {
        const struct sockaddr_in6 *address = (const struct sockaddr_in6 *)&storage;
        (void)inet6_ntoa_r(address->sin6_addr, out, cap);
    }
}

static bool authorized(httpd_req_t *req)
{
    char provided[80];
    if (httpd_req_get_hdr_value_str(req, CONTROL_TOKEN_HEADER, provided, sizeof(provided)) !=
        ESP_OK) {
        return false;
    }
    char expected[80];
    (void)control_token_load(expected, sizeof(expected));
    return expected[0] != '\0' && strcmp(provided, expected) == 0;
}

/* Read the whole request body into `buffer`. Returns the length or -1. */
static int recv_body(httpd_req_t *req, char *buffer, size_t cap)
{
    if (req->content_len <= 0 || (size_t)req->content_len >= cap) {
        return -1;
    }
    size_t received = 0;
    while (received < (size_t)req->content_len) {
        int read = httpd_req_recv(req, buffer + received, (size_t)req->content_len - received);
        if (read == HTTPD_SOCK_ERR_TIMEOUT) {
            continue;
        }
        if (read <= 0) {
            return -1;
        }
        received += (size_t)read;
    }
    buffer[received] = '\0';
    return (int)received;
}

/* Gate for every handler: authenticate, and audit failures. */
static bool guard(httpd_req_t *req, const char *action)
{
    if (authorized(req)) {
        return true;
    }
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record(action, "unauthorized", ip);
    (void)send_json(req, "401 Unauthorized", BODY_UNAUTHORIZED);
    return false;
}

static esp_err_t send_chunked(httpd_req_t *req, const char *status, const char *content_type,
                              const char *data, size_t length)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, content_type);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_send_chunk(req, data, (ssize_t)length);
    if (err == ESP_OK) {
        err = httpd_resp_send_chunk(req, NULL, 0);
    }
    return err;
}

/* --------------------------------------------------------------- handlers */

static esp_err_t h_status(httpd_req_t *req)
{
    if (!guard(req, "status")) {
        return ESP_OK;
    }
    const sc_config_t *cfg = app_config();
    app_network_t network = app_state_network();
    app_sensor_t sensor = app_state_sensor();
    char i2c[128];
    char body[1024]; /* led/ledMode/ledAvailable plus a maximal pcUrl */
    int length = snprintf(
        body, sizeof(body),
        "{\"uptimeSec\":%lld,\"wifi\":{\"connected\":%s,\"ip\":\"%s\",\"rssi\":%d},"
        "\"devices\":%d,\"generatedAt\":%lld,\"roundMillis\":%u,"
        "\"sensor\":{\"ok\":%s,\"temperatureC\":%.1f,\"humidityPct\":%.1f},"
        "\"i2c\":[%s],\"led\":\"%s\",\"ledMode\":\"%s\",\"ledAvailable\":%s,"
        "\"config\":{\"refreshSeconds\":%d,\"consoles\":%d,\"bleDevices\":%d,"
        "\"aliases\":%d,\"pcFeed\":%s,\"pcUrl\":\"%s\"},"
        "\"configRollbackPending\":%s}",
        (long long)(esp_timer_get_time() / 1000000), network.wifi_connected ? "true" : "false",
        network.ip, network.rssi, app_state_device_count(),
        (long long)app_state_generated_at(), (unsigned)app_state_last_round_millis(),
        sensor.ok ? "true" : "false", sensor.temperature_c, sensor.humidity_pct,
        app_state_i2c_list(i2c, sizeof(i2c)), sc_led_name(status_led_shown()),
        status_led_mode_name(), status_led_available() ? "true" : "false",
        cfg->refresh_seconds, cfg->console_count, cfg->ble_count, cfg->aliases.count,
        cfg->pc_enabled ? "true" : "false", cfg->pc_url.has ? cfg->pc_url.v : "",
        config_guard_pending() ? "true" : "false");
    if (length <= 0 || (size_t)length >= sizeof(body)) {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"status too large\"}");
    }
    return send_json(req, "200 OK", body);
}

static esp_err_t h_scan(httpd_req_t *req)
{
    if (!guard(req, "scan")) {
        return ESP_OK;
    }
    static char *body;
    if (body == NULL) {
        body = (char *)sc_psram_alloc(2048);
        if (body == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }

    static uint8_t addresses[16];
    int i2c_count = ssd1315_i2c_scan(addresses, 16);

    static char names[16][32];
    static char addrs[16][18];
    static int8_t rssi[16];
    int ble_count = ble_collector_scan_list(16, names, addrs, rssi);

    size_t used = 0;
    int written = snprintf(body, 2048, "{\"i2c\":[");
    used = (size_t)written;
    for (int i = 0; i < i2c_count && used < 1900; ++i) {
        written = snprintf(body + used, 2048 - used, "%s\"0x%02X\"", i == 0 ? "" : ",",
                           (unsigned)addresses[i]);
        used += (size_t)written;
    }
    written = snprintf(body + used, 2048 - used, "],\"ble\":[");
    used += (size_t)written;
    for (int i = 0; i < ble_count && used < 1900; ++i) {
        written = snprintf(body + used, 2048 - used,
                           "%s{\"address\":\"%s\",\"name\":\"%s\",\"rssi\":%d}",
                           i == 0 ? "" : ",", addrs[i], names[i], (int)rssi[i]);
        used += (size_t)written;
    }
    if (used < 2040) {
        written = snprintf(body + used, 2048 - used, "]}");
        used += (size_t)written;
    }
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("scan", "ok", ip);
    return send_body(req, "200 OK", "application/json; charset=utf-8", body, used);
}

static void delayed_restart_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(2000));
    ESP_LOGW(TAG, "restarting on /control/reboot");
    esp_restart();
}

static esp_err_t h_reboot(httpd_req_t *req)
{
    if (!guard(req, "reboot")) {
        return ESP_OK;
    }
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("reboot", "ok", ip);
    esp_err_t err = send_json(req, "200 OK", "{\"ok\":true,\"rebootIn\":2}");
    (void)xTaskCreate(delayed_restart_task, "sc_reboot", 2048, NULL, 3, NULL);
    return err;
}

static esp_err_t h_config(httpd_req_t *req)
{
    if (!guard(req, "config")) {
        return ESP_OK;
    }
    static char *body;
    if (body == NULL) {
        body = (char *)sc_psram_alloc(CONTROL_BODY_MAX);
        if (body == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }
    int length = recv_body(req, body, CONTROL_BODY_MAX);
    if (length <= 0) {
        return send_json(req, "400 Bad Request", BODY_BAD_REQUEST);
    }
    cJSON *root = cJSON_ParseWithLength(body, (size_t)length);
    if (root == NULL || !cJSON_IsObject(root)) {
        if (root != NULL) {
            cJSON_Delete(root);
        }
        return send_json(req, "400 Bad Request", "{\"error\":\"body is not a JSON object\"}");
    }
    cJSON_Delete(root);

    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    esp_err_t err = config_guard_apply(body, (size_t)length);
    if (err != ESP_OK) {
        audit_record("config", "write-failed", ip);
        return send_json(req, "500 Internal Server Error", "{\"error\":\"write failed\"}");
    }
    audit_record("config", "ok", ip);
    char response[96];
    (void)snprintf(response, sizeof(response),
                   "{\"ok\":true,\"bytes\":%d,\"rollbackInSeconds\":90}", length);
    return send_json(req, "200 OK", response);
}

static esp_err_t h_led(httpd_req_t *req)
{
    if (!guard(req, "led")) {
        return ESP_OK;
    }
    static char *body;
    if (body == NULL) {
        body = (char *)sc_psram_alloc(1024);
        if (body == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }
    int length = recv_body(req, body, 1024);
    cJSON *root = length > 0 ? cJSON_ParseWithLength(body, (size_t)length) : NULL;
    if (root == NULL || !cJSON_IsObject(root)) {
        if (root != NULL) {
            cJSON_Delete(root);
        }
        return send_json(req, "400 Bad Request", BODY_BAD_REQUEST);
    }

    bool automatic = cJSON_IsTrue(cJSON_GetObjectItemCaseSensitive(root, "auto"));
    const cJSON *color = cJSON_GetObjectItemCaseSensitive(root, "color");
    sc_led_state_t state = SC_LED_OFF;
    const char *audit_name = "auto";
    if (automatic) {
        /* Hands control back and repaints the automatic colour immediately. */
        status_led_set_manual(false, SC_LED_OFF);
    } else if (cJSON_IsString(color) && sc_led_state_from_name(color->valuestring, &state)) {
        audit_name = color->valuestring;
        status_led_set_manual(true, state);
    } else if (cJSON_IsString(color)) {
        cJSON_Delete(root);
        return send_json(req, "400 Bad Request", "{\"error\":\"unknown color\"}");
    } else {
        cJSON_Delete(root);
        return send_json(req, "400 Bad Request", "{\"error\":\"color or auto required\"}");
    }
    cJSON_Delete(root);

    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("led", audit_name, ip);

    /* Report what the pixel is doing and who decided it, not what was asked:
     * those differ as soon as a manual colour is in force. */
    char response[96];
    (void)snprintf(response, sizeof(response), "{\"ok\":true,\"led\":\"%s\",\"ledMode\":\"%s\"}",
                   sc_led_name(status_led_shown()), status_led_mode_name());
    return send_json(req, "200 OK", response);
}

static esp_err_t h_token(httpd_req_t *req)
{
    if (!guard(req, "token")) {
        return ESP_OK;
    }
    static char *body;
    if (body == NULL) {
        body = (char *)sc_psram_alloc(512);
        if (body == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }
    int length = recv_body(req, body, 512);
    cJSON *root = length > 0 ? cJSON_ParseWithLength(body, (size_t)length) : NULL;
    const cJSON *token = root != NULL ? cJSON_GetObjectItemCaseSensitive(root, "token") : NULL;
    if (root == NULL || !cJSON_IsString(token)) {
        if (root != NULL) {
            cJSON_Delete(root);
        }
        return send_json(req, "400 Bad Request", "{\"error\":\"token required\"}");
    }
    size_t token_length = strlen(token->valuestring);
    if (token_length < 4 || token_length > 63) {
        cJSON_Delete(root);
        return send_json(req, "400 Bad Request", "{\"error\":\"token must be 4..63 chars\"}");
    }
    bool saved = control_token_save(token->valuestring);
    cJSON_Delete(root);
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("token", saved ? "rotated" : "failed", ip);
    if (!saved) {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"nvs write failed\"}");
    }
    return send_json(req, "200 OK", "{\"ok\":true}");
}

static esp_err_t h_log(httpd_req_t *req)
{
    if (!guard(req, "log")) {
        return ESP_OK;
    }
    static char *buffer;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(16384);
        if (buffer == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }
    size_t length = log_ring_dump(buffer, 16384, 128);
    return send_chunked(req, "200 OK", "text/plain; charset=utf-8", buffer, length);
}

static esp_err_t h_audit(httpd_req_t *req)
{
    if (!guard(req, "audit")) {
        return ESP_OK;
    }
    static char *buffer;
    if (buffer == NULL) {
        buffer = (char *)sc_psram_alloc(4096);
        if (buffer == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }
    size_t length = audit_render_json(buffer, 4096);
    return send_body(req, "200 OK", "application/json; charset=utf-8", buffer, length);
}

static const esp_partition_t *coredump_partition(void)
{
    return esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_DATA_COREDUMP,
                                    NULL);
}

static esp_err_t h_coredump_get(httpd_req_t *req)
{
    if (!guard(req, "coredump")) {
        return ESP_OK;
    }
    size_t address = 0;
    size_t size = 0;
    if (esp_core_dump_image_get(&address, &size) != ESP_OK || size == 0) {
        return send_json(req, "404 Not Found", BODY_NO_DUMP);
    }
    const esp_partition_t *partition = coredump_partition();
    if (partition == NULL) {
        return send_json(req, "404 Not Found", BODY_NO_DUMP);
    }
    const size_t offset = address - partition->address;
    if (offset >= partition->size || offset + size > partition->size) {
        return send_json(req, "404 Not Found", BODY_NO_DUMP);
    }

    static uint8_t *chunk;
    if (chunk == NULL) {
        chunk = (uint8_t *)sc_psram_alloc(1024);
        if (chunk == NULL) {
            return send_json(req, "500 Internal Server Error", "{\"error\":\"out of memory\"}");
        }
    }

    mbedtls_sha256_context sha;
    mbedtls_sha256_init(&sha);
    mbedtls_sha256_starts(&sha, 0);
    for (size_t done = 0; done < size;) {
        size_t want = size - done < 1024 ? size - done : 1024;
        if (esp_partition_read(partition, offset + done, chunk, want) != ESP_OK) {
            mbedtls_sha256_free(&sha);
            return send_json(req, "500 Internal Server Error", "{\"error\":\"flash read failed\"}");
        }
        mbedtls_sha256_update(&sha, chunk, want);
        done += want;
    }
    uint8_t digest[32];
    mbedtls_sha256_finish(&sha, digest);
    mbedtls_sha256_free(&sha);

    char digest_text[65];
    for (int i = 0; i < 32; ++i) {
        (void)snprintf(digest_text + i * 2, 3, "%02x", digest[i]);
    }
    char size_text[24];
    (void)snprintf(size_text, sizeof(size_text), "%u", (unsigned)size);

    httpd_resp_set_status(req, "200 OK");
    httpd_resp_set_type(req, "application/octet-stream");
    httpd_resp_set_hdr(req, "X-Dump-SHA256", digest_text);
    httpd_resp_set_hdr(req, "X-Dump-Size", size_text);
    httpd_resp_set_hdr(req, "Content-Disposition", "attachment; filename=coredump.bin");

    esp_err_t err = ESP_OK;
    for (size_t done = 0; done < size && err == ESP_OK;) {
        size_t want = size - done < 1024 ? size - done : 1024;
        if (esp_partition_read(partition, offset + done, chunk, want) != ESP_OK) {
            break;
        }
        err = httpd_resp_send_chunk(req, (const char *)chunk, (ssize_t)want);
        done += want;
    }
    if (err == ESP_OK) {
        err = httpd_resp_send_chunk(req, NULL, 0);
    }
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("coredump", "download", ip);
    return err;
}

static esp_err_t h_coredump_delete(httpd_req_t *req)
{
    if (!guard(req, "coredump-delete")) {
        return ESP_OK;
    }
    const esp_partition_t *partition = coredump_partition();
    if (partition == NULL) {
        return send_json(req, "404 Not Found", BODY_NO_DUMP);
    }
    esp_err_t err = esp_partition_erase_range(partition, 0, partition->size);
    char ip[48];
    peer_ip(req, ip, sizeof(ip));
    audit_record("coredump", err == ESP_OK ? "erased" : "erase-failed", ip);
    if (err != ESP_OK) {
        return send_json(req, "500 Internal Server Error", "{\"error\":\"erase failed\"}");
    }
    return send_json(req, "200 OK", "{\"ok\":true}");
}

/* --------------------------------------------------------------- routing */

static const httpd_uri_t s_routes[] = {
    {.uri = "/control/status", .method = HTTP_GET, .handler = h_status, .user_ctx = NULL},
    {.uri = "/control/scan", .method = HTTP_GET, .handler = h_scan, .user_ctx = NULL},
    {.uri = "/control/reboot", .method = HTTP_POST, .handler = h_reboot, .user_ctx = NULL},
    {.uri = "/control/config", .method = HTTP_POST, .handler = h_config, .user_ctx = NULL},
    {.uri = "/control/led", .method = HTTP_POST, .handler = h_led, .user_ctx = NULL},
    {.uri = "/control/token", .method = HTTP_POST, .handler = h_token, .user_ctx = NULL},
    {.uri = "/control/log", .method = HTTP_GET, .handler = h_log, .user_ctx = NULL},
    {.uri = "/control/coredump", .method = HTTP_GET, .handler = h_coredump_get, .user_ctx = NULL},
    {.uri = "/control/coredump", .method = HTTP_DELETE, .handler = h_coredump_delete,
     .user_ctx = NULL},
    {.uri = "/control/audit", .method = HTTP_GET, .handler = h_audit, .user_ctx = NULL},
};

/* The server sizes its handler table with this count: see sc_route_counts.h. */
_Static_assert(sizeof(s_routes) / sizeof(s_routes[0]) == SC_CONTROL_ROUTE_COUNT,
               "s_routes must contain SC_CONTROL_ROUTE_COUNT routes (core/sc_route_counts.h)");

esp_err_t control_api_register(httpd_handle_t server)
{
    for (size_t i = 0; i < sizeof(s_routes) / sizeof(s_routes[0]); ++i) {
        esp_err_t err = httpd_register_uri_handler(server, &s_routes[i]);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "register %s: %s", s_routes[i].uri, esp_err_to_name(err));
            return err;
        }
    }
    ESP_LOGI(TAG, "control API registered (%u routes)",
             (unsigned)(sizeof(s_routes) / sizeof(s_routes[0])));
    return ESP_OK;
}
