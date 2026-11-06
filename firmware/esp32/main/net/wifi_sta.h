/*
 * wifi_sta -- WiFi station + SNTP + mDNS.
 *
 * Two separate steps on purpose:
 *   wifi_init()  always runs: esp_netif + event loop + lwIP, so the HTTP server
 *                can bind even on a board with no credentials provisioned yet.
 *   wifi_start() configures the AP and starts the driver; it is only called when
 *                NVS actually holds credentials.
 *
 * Credentials come from NVS only (see config_store) and are never logged.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

typedef void (*wifi_state_cb_t)(bool connected, const char *ip, int rssi, void *user);

/* Brings up the network stack (netif + event loop + lwIP). Idempotent.
 * Must run before http_server_start() even when there are no credentials. */
esp_err_t wifi_init(void);

/* Configures the station with the given credentials and starts the driver.
 * Calls wifi_init() first when needed. */
esp_err_t wifi_start(const char *ssid, const char *password, wifi_state_cb_t callback, void *user);

typedef struct {
    char ssid[33];
    int8_t rssi;
    uint8_t channel;
    bool secured;
} wifi_ap_info_t;

/* Blocking scan (2.4 GHz only -- the ESP32-S3 radio has no 5 GHz support).
 * Starts the driver if it is not running yet. Returns the number of APs or -1. */
int wifi_scan(wifi_ap_info_t *out, int max);

bool wifi_is_connected(void);
int wifi_rssi(void);
/* Current IPv4 address as a string, or "" when not connected. */
const char *wifi_ip(void);

/* Starts mDNS with `statuscast.local`; safe to call once networking is up. */
esp_err_t wifi_start_mdns(void);

/* Reconnect with new credentials at runtime (console `net ssid/pass`). */
esp_err_t wifi_reconnect(const char *ssid, const char *password);
