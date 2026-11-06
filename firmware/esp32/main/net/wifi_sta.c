#include "wifi_sta.h"

#include <stdio.h>
#include <string.h>

#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_netif_sntp.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/timers.h"
#include "mdns.h"

static const char *TAG = "sc_wifi";

#define WIFI_HOSTNAME "statuscast"
#define WIFI_MAX_BACKOFF_SECONDS 30

static wifi_state_cb_t s_callback;
static void *s_callback_user;
static TimerHandle_t s_retry_timer;
static int s_retry_count;
static bool s_stack_ready;
static bool s_driver_started;

static volatile bool s_connected;
static volatile int s_rssi;
static volatile bool s_sntp_started;
static char s_ip[16];
static char s_ssid[64];
static char s_password[64];

static void notify(void)
{
    if (s_callback != NULL) {
        s_callback(s_connected, s_ip, s_rssi, s_callback_user);
    }
}

static void refresh_rssi(void)
{
    wifi_ap_record_t record;
    if (esp_wifi_sta_get_ap_info(&record) == ESP_OK) {
        s_rssi = record.rssi;
    }
}

static void retry_timer_cb(TimerHandle_t timer)
{
    (void)timer;
    if (!s_driver_started) {
        return;
    }
    ESP_LOGI(TAG, "reconnecting (attempt %d)", s_retry_count);
    esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_connect: %s", esp_err_to_name(err));
    }
}

static void schedule_retry(void)
{
    s_retry_count += 1;
    int shift = s_retry_count > 5 ? 5 : s_retry_count;
    uint32_t seconds = 1u << shift;
    if (seconds > WIFI_MAX_BACKOFF_SECONDS) {
        seconds = WIFI_MAX_BACKOFF_SECONDS;
    }
    if (s_retry_timer != NULL) {
        (void)xTimerChangePeriod(s_retry_timer, pdMS_TO_TICKS(seconds * 1000), 0);
    }
}

static void start_sntp(void)
{
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_err_t err = esp_netif_sntp_init(&config);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "SNTP init failed: %s", esp_err_to_name(err));
    }
}

static void apply_station_config(void)
{
    wifi_config_t config = {0};
    (void)snprintf((char *)config.sta.ssid, sizeof(config.sta.ssid), "%.31s", s_ssid);
    (void)snprintf((char *)config.sta.password, sizeof(config.sta.password), "%.31s", s_password);
    config.sta.threshold.authmode =
        (s_password[0] == '\0') ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    config.sta.pmf_cfg.capable = true;
    config.sta.pmf_cfg.required = false;
    esp_err_t err = esp_wifi_set_config(WIFI_IF_STA, &config);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "esp_wifi_set_config: %s", esp_err_to_name(err));
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    (void)data;

    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_ssid[0] != '\0') {
            esp_err_t err = esp_wifi_connect();
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "initial connect failed: %s", esp_err_to_name(err));
            }
        }
        return;
    }

    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_connected = false;
        s_ip[0] = '\0';
        s_rssi = 0;
        notify();
        schedule_retry();
        return;
    }

    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = (const ip_event_got_ip_t *)data;
        (void)snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&event->ip_info.ip));
        s_connected = true;
        s_retry_count = 0;
        if (s_retry_timer != NULL) {
            (void)xTimerStop(s_retry_timer, 0);
        }
        refresh_rssi();
        ESP_LOGI(TAG, "got ip %s (rssi %d)", s_ip, s_rssi);
        if (!s_sntp_started) {
            start_sntp();
            s_sntp_started = true;
        }
        notify();
    }
}

esp_err_t wifi_init(void)
{
    if (s_stack_ready) {
        return ESP_OK;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    esp_netif_t *netif = esp_netif_create_default_wifi_sta();
    if (netif != NULL) {
        (void)esp_netif_set_hostname(netif, WIFI_HOSTNAME);
    }

    wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init_config));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                        wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                        wifi_event_handler, NULL, NULL));

    if (s_retry_timer == NULL) {
        s_retry_timer = xTimerCreate("sc_wifi_retry", pdMS_TO_TICKS(1000), pdFALSE, NULL,
                                     retry_timer_cb);
    }

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    s_stack_ready = true;
    return ESP_OK;
}

esp_err_t wifi_start(const char *ssid, const char *password, wifi_state_cb_t callback, void *user)
{
    if (callback != NULL) {
        s_callback = callback;
        s_callback_user = user;
    }
    s_retry_count = 0;
    (void)snprintf(s_ssid, sizeof(s_ssid), "%.63s", ssid != NULL ? ssid : "");
    (void)snprintf(s_password, sizeof(s_password), "%.63s", password != NULL ? password : "");

    esp_err_t err = wifi_init();
    if (err != ESP_OK) {
        return err;
    }
    apply_station_config();

    if (!s_driver_started) {
        err = esp_wifi_start();
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_wifi_start: %s", esp_err_to_name(err));
            return err;
        }
        s_driver_started = true;
    } else if (s_ssid[0] != '\0') {
        (void)esp_wifi_disconnect();
        (void)esp_wifi_connect();
    }

    ESP_LOGI(TAG, "station configured, ssid \"%s\"", s_ssid);
    return ESP_OK;
}

bool wifi_is_connected(void)
{
    return s_connected;
}

int wifi_rssi(void)
{
    if (s_connected) {
        refresh_rssi();
    }
    return s_rssi;
}

const char *wifi_ip(void)
{
    return s_ip;
}

esp_err_t wifi_reconnect(const char *ssid, const char *password)
{
    if (ssid != NULL) {
        (void)snprintf(s_ssid, sizeof(s_ssid), "%.63s", ssid);
    }
    if (password != NULL) {
        (void)snprintf(s_password, sizeof(s_password), "%.63s", password);
    }

    esp_err_t err = wifi_init();
    if (err != ESP_OK) {
        return err;
    }
    apply_station_config();

    if (!s_driver_started) {
        err = esp_wifi_start();
        if (err != ESP_OK) {
            return err;
        }
        s_driver_started = true;
        return ESP_OK;
    }
    (void)esp_wifi_disconnect();
    return esp_wifi_connect();
}

esp_err_t wifi_start_mdns(void)
{
    esp_err_t err = mdns_init();
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "mdns_init: %s", esp_err_to_name(err));
        return err;
    }
    (void)mdns_hostname_set(WIFI_HOSTNAME);
    (void)mdns_instance_name_set("StatusCast ESP32");
    (void)mdns_service_add(NULL, "_http", "_tcp", 5099, NULL, 0);
    ESP_LOGI(TAG, "mDNS: " WIFI_HOSTNAME ".local");
    return ESP_OK;
}
