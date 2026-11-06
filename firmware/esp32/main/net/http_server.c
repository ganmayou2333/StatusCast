#include "http_server.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"

#include "app_state.h"
#include "ui/status_led.h"

static const char *TAG = "sc_http";

#define SC_BODY_MAX 8192

static const char BODY_NOT_READY[] = "{\"error\":\"status snapshot not ready\"}";
static const char BODY_NOT_FOUND[] = "{\"error\":\"not found\"}";
static const char BODY_METHOD_NOT_ALLOWED[] = "{\"error\":\"method not allowed\"}";
static const char BODY_HEALTH_NOT_READY[] =
    "{\"ok\":false,\"deviceCount\":null,\"generatedAt\":null,\"schema\":null}";

static httpd_handle_t s_server;

static esp_err_t respond(httpd_req_t *req, const char *status, const char *body, size_t length)
{
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json; charset=utf-8");
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    return httpd_resp_send(req, body, (ssize_t)length);
}

static esp_err_t respond_literal(httpd_req_t *req, const char *status, const char *body)
{
    return respond(req, status, body, strlen(body));
}

static esp_err_t handle_status(httpd_req_t *req)
{
    if (!app_state_ready()) {
        return respond_literal(req, "503 Service Unavailable", BODY_NOT_READY);
    }
    char *body = (char *)malloc(SC_BODY_MAX);
    if (body == NULL) {
        ESP_LOGE(TAG, "out of memory building /status/ response");
        return respond_literal(req, "503 Service Unavailable", BODY_NOT_READY);
    }
    size_t length = app_state_copy(body, SC_BODY_MAX);
    if (length == 0) {
        free(body);
        return respond_literal(req, "503 Service Unavailable", BODY_NOT_READY);
    }
    esp_err_t err = respond(req, "200 OK", body, length);
    free(body);
    return err;
}

static esp_err_t handle_health(httpd_req_t *req)
{
    if (!app_state_ready()) {
        return respond_literal(req, "503 Service Unavailable", BODY_HEALTH_NOT_READY);
    }
    char body[192];
    int count = app_state_device_count();
    int64_t generated_at = app_state_generated_at();
    int length = snprintf(body, sizeof(body),
                          "{\"ok\":true,\"deviceCount\":%d,\"generatedAt\":%lld,\"schema\":3}",
                          count, (long long)generated_at);
    if (length <= 0 || (size_t)length >= sizeof(body)) {
        return respond_literal(req, "503 Service Unavailable", BODY_HEALTH_NOT_READY);
    }
    return respond(req, "200 OK", body, (size_t)length);
}

/* Diagnostic-only endpoint (not part of the schema-3 contract): sensor reading,
 * I2C inventory and timing, so hardware can be checked over WiFi without the
 * flaky USB-serial bridge. */
static esp_err_t handle_diag(httpd_req_t *req)
{
    char i2c[128];
    app_sensor_t sensor = app_state_sensor();
    char body[448];
    int length = snprintf(body, sizeof(body),
                          "{\"deviceCount\":%d,\"generatedAt\":%lld,"
                          "\"roundMillis\":%u,\"i2c\":[%s],"
                          "\"sensor\":{\"ok\":%s,\"temperatureC\":%.1f,"
                          "\"humidityPct\":%.1f},\"led\":\"%s\"}",
                          app_state_device_count(), (long long)app_state_generated_at(),
                          (unsigned)app_state_last_round_millis(),
                          app_state_i2c_list(i2c, sizeof(i2c)),
                          sensor.ok ? "true" : "false", sensor.temperature_c,
                          sensor.humidity_pct,
                          status_led_name(status_led_get()));
    if (length <= 0 || (size_t)length >= sizeof(body)) {
        return respond_literal(req, "503 Service Unavailable", BODY_NOT_READY);
    }
    return respond(req, "200 OK", body, (size_t)length);
}

static esp_err_t handle_not_found(httpd_req_t *req)
{
    return respond_literal(req, "404 Not Found", BODY_NOT_FOUND);
}

static esp_err_t handle_method_not_allowed(httpd_req_t *req)
{
    return respond_literal(req, "405 Method Not Allowed", BODY_METHOD_NOT_ALLOWED);
}

static esp_err_t handle_uri_not_found(httpd_req_t *req, httpd_err_code_t error)
{
    (void)error;
    return handle_not_found(req);
}

static esp_err_t handle_uri_method_not_allowed(httpd_req_t *req, httpd_err_code_t error)
{
    (void)error;
    return handle_method_not_allowed(req);
}

static const httpd_uri_t s_routes[] = {
    {.uri = "/status/", .method = HTTP_GET, .handler = handle_status, .user_ctx = NULL},
    {.uri = "/health", .method = HTTP_GET, .handler = handle_health, .user_ctx = NULL},
    {.uri = "/diag", .method = HTTP_GET, .handler = handle_diag, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_GET, .handler = handle_not_found, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_POST, .handler = handle_method_not_allowed, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_PUT, .handler = handle_method_not_allowed, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_DELETE, .handler = handle_method_not_allowed, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_PATCH, .handler = handle_method_not_allowed, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_OPTIONS, .handler = handle_method_not_allowed, .user_ctx = NULL},
    {.uri = "/*", .method = HTTP_HEAD, .handler = handle_method_not_allowed, .user_ctx = NULL},
};

esp_err_t http_server_start(uint16_t port)
{
    if (s_server != NULL) {
        return ESP_OK;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = port;
    config.max_uri_handlers = (uint16_t)(sizeof(s_routes) / sizeof(s_routes[0]));
    config.stack_size = 8192;
    config.lru_purge_enable = true;
    // Needed for the catch-all routes ("/*").
    config.uri_match_fn = httpd_uri_match_wildcard;

    esp_err_t err = httpd_start(&s_server, &config);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start: %s", esp_err_to_name(err));
        s_server = NULL;
        return err;
    }

    for (size_t i = 0; i < sizeof(s_routes) / sizeof(s_routes[0]); ++i) {
        err = httpd_register_uri_handler(s_server, &s_routes[i]);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "register %s: %s", s_routes[i].uri, esp_err_to_name(err));
        }
    }
    (void)httpd_register_err_handler(s_server, HTTPD_404_NOT_FOUND, handle_uri_not_found);
    (void)httpd_register_err_handler(s_server, HTTPD_405_METHOD_NOT_ALLOWED,
                                     handle_uri_method_not_allowed);

    ESP_LOGI(TAG, "listening on 0.0.0.0:%u", (unsigned)port);
    return ESP_OK;
}

void http_server_stop(void)
{
    if (s_server != NULL) {
        (void)httpd_stop(s_server);
        s_server = NULL;
    }
}
