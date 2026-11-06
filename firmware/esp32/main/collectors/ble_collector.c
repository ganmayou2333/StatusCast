/*
 * ble_collector -- NimBLE central implementation of the Python BLE collector.
 *
 * Lifecycle:
 *   - ble_collector_init() starts NimBLE once (controller + host task) and waits
 *     for the host/controller sync event.  Calls after the first one are no-ops.
 *   - ble_collector_run() runs on the collector task.  It starts one shared
 *     discovery procedure, lets the host task collect advertisements for
 *     4 seconds, cancels it, and then walks the configured entries in config
 *     order: connect, GATT discovery/read/subscribe, disconnect.  The post-scan
 *     work is bounded by a wall-clock budget so a round stays inside 30 s.
 *   - ble_collector_scan_list() runs the same 4 s scan for the serial console
 *     and reports every advertising device it saw (deduplicated by address).
 *
 * Threading model: the collector task owns every "start" call and waits on an
 * event group; the NimBLE host task writes the small result slots and signals
 * the corresponding bit.  Nothing blocks the host task.  A mutex serialises the
 * collector round and the console scan so they never share scan state.
 */
#include "ble_collector.h"

#include <stdio.h>
#include <string.h>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "host/ble_hs.h"
#include "host/util/util.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "nvs_flash.h"

static const char *TAG = "sc_ble";

/* Bluetooth SIG assigned numbers: Battery Service and Heart Rate Service. */
#define BLE_SVC_BATTERY_UUID 0x180Fu
#define BLE_CHR_BATTERY_LEVEL_UUID 0x2A19u
#define BLE_SVC_HEART_RATE_UUID 0x180Du
#define BLE_CHR_HEART_RATE_MEASUREMENT_UUID 0x2A37u

/* Timing budgets, copied from collectors/ble_collector.py. */
#define BLE_SCAN_WINDOW_MS 4000
#define BLE_SCAN_STOP_MS 1000
#define BLE_SYNC_WAIT_MS 10000
#define BLE_CONNECT_TIMEOUT_MS 5000
#define BLE_GATT_TIMEOUT_MS 2000
#define BLE_DISCONNECT_WAIT_MS 2000

/* Wall-clock budget for one collector round, measured after the shared scan:
 * the round must stay inside the 30 s refresh budget (4 s scan + 20 s here plus
 * the tail of a device that was already being connected when it expired). */
#define BLE_ROUND_BUDGET_MS 20000

/* Console scan list: bounded storage, names and addresses match the API. */
#define BLE_SCAN_LIST_MAX 16
#define BLE_SCAN_LIST_NAME_LEN 32
#define BLE_SCAN_LIST_ADDR_LEN 18
/* rssi[i] value used when the controller reported no measurement. */
#define BLE_RSSI_UNKNOWN (-127)

#define BLE_ADV_NAME_MAX 40
#define BLE_BATTERY_READ_MAX 4
#define BLE_HR_NOTIFY_MAX 8

/* Human-facing status text for the two non-success, non-error states. */
#define BLE_STATUS_NOT_CONNECTED "已发现未连接"
#define BLE_STATUS_NOT_FOUND "未发现"

/* Event bits shared between the collector task and the NimBLE host task. */
#define EV_SYNC (1u << 0)
#define EV_SCAN_DONE (1u << 1)
#define EV_CONNECT (1u << 2)
#define EV_LINK_DOWN (1u << 3)
#define EV_SVC_DONE (1u << 4)
#define EV_CHR_DONE (1u << 5)
#define EV_DSC_DONE (1u << 6)
#define EV_READ_DONE (1u << 7)
#define EV_WRITE_DONE (1u << 8)
#define EV_HR_NOTIFY (1u << 9)

typedef struct {
    bool has_mac;
    /* Six octets in printed order: mac[0] is the first octet of AA:BB:CC:DD:EE:FF. */
    uint8_t mac[6];
    bool has_name_filter;
    char name_filter[SC_STR_LEN];
} ble_match_t;

typedef struct {
    bool found;
    ble_addr_t addr;
    bool has_rssi;
    int32_t rssi;
    bool has_name;
    char name[BLE_ADV_NAME_MAX];
} ble_scan_slot_t;

typedef struct {
    bool init_attempted;
    bool inited;
    volatile bool synced;
    EventGroupHandle_t events;
    SemaphoreHandle_t lock;

    /* Scan state: only the collector task changes scan_count / scanning /
     * list_mode, the host task just reads them from the GAP callback. */
    volatile bool scanning;
    volatile bool list_mode;
    int scan_count;

    /* Connection and GATT hand-off slots. */
    volatile uint16_t conn_handle;
    volatile bool connected;
    volatile int connect_status;
    volatile int disconnect_reason;

    volatile bool svc_found;
    volatile uint16_t svc_start;
    volatile uint16_t svc_end;

    volatile bool chr_found;
    volatile uint16_t chr_val;

    volatile bool dsc_found;
    volatile uint16_t dsc_handle;

    volatile int read_status;
    volatile int write_status;

    volatile bool hr_received;
    volatile int hr_len;
    uint8_t hr_buf[BLE_HR_NOTIFY_MAX];

    /* Set when a GATT procedure timed out (the stack is then likely still busy
     * with it, so the rest of that device's steps are skipped). */
    volatile bool gatt_stalled;
} ble_state_t;

typedef struct {
    uint8_t *buf;
    size_t cap;
    int len;
} ble_read_target_t;

typedef struct {
    bool linked;
    bool has_battery;
    int32_t battery;
    bool has_heart_rate;
    int32_t heart_rate;
} ble_reading_t;

/* One entry of the console-facing scan list (deduplicated by address). */
typedef struct {
    ble_addr_t addr;
    char name[BLE_SCAN_LIST_NAME_LEN];
    bool has_rssi;
    int32_t rssi;
} ble_list_entry_t;

static ble_state_t s_ble;
static ble_match_t s_match[SC_MAX_BLE_DEVICES];
static ble_scan_slot_t s_scan[SC_MAX_BLE_DEVICES];
static ble_list_entry_t s_list[BLE_SCAN_LIST_MAX];
static int s_list_count;
static int s_list_limit;

/* --------------------------------------------------------------- utilities */

static void ble_clear_events(EventBits_t bits)
{
    if (s_ble.events != NULL) {
        (void)xEventGroupClearBits(s_ble.events, bits);
    }
}

static void ble_set_event(EventBits_t bit)
{
    if (s_ble.events != NULL) {
        (void)xEventGroupSetBits(s_ble.events, bit);
    }
}

/* Wait for one of `want`, also waking up early when the link goes down. */
static bool ble_wait_for(EventBits_t want, uint32_t timeout_ms)
{
    EventBits_t got = xEventGroupWaitBits(s_ble.events, want | EV_LINK_DOWN, pdFALSE, pdFALSE,
                                          (TickType_t)pdMS_TO_TICKS(timeout_ms));
    return (got & want) != 0;
}

/* Monotonic milliseconds; only used for the per-round wall-clock budget. */
static int64_t ble_now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static char ble_lower_ascii(char c)
{
    if (c >= 'A' && c <= 'Z') {
        return (char)(c - 'A' + 'a');
    }
    return c;
}

static void ble_copy_upper(char *dst, size_t cap, const char *src)
{
    size_t i = 0;

    if (dst == NULL || cap == 0) {
        return;
    }
    for (; src != NULL && src[i] != '\0' && i + 1 < cap; ++i) {
        char c = src[i];
        if (c >= 'a' && c <= 'z') {
            c = (char)(c - 'a' + 'A');
        }
        dst[i] = c;
    }
    dst[i] = '\0';
}

static int ble_hex_value(char c)
{
    if (c >= '0' && c <= '9') {
        return c - '0';
    }
    if (c >= 'a' && c <= 'f') {
        return c - 'a' + 10;
    }
    if (c >= 'A' && c <= 'F') {
        return c - 'A' + 10;
    }
    return -1;
}

/* Parse "AA:BB:CC:DD:EE:FF" (case-insensitive, ':' or '-' separators) into six
 * octets in printed order. */
static bool ble_parse_mac(const char *text, uint8_t out[6])
{
    int nibble = 0;
    int high = 0;
    int octets = 0;

    if (text == NULL) {
        return false;
    }
    for (const char *p = text; *p != '\0'; ++p) {
        int value;

        if (*p == ':' || *p == '-') {
            continue;
        }
        value = ble_hex_value(*p);
        if (value < 0) {
            return false;
        }
        if (nibble == 0) {
            high = value;
            nibble = 1;
        } else {
            if (octets >= 6) {
                return false;
            }
            out[octets] = (uint8_t)((high << 4) | value);
            octets += 1;
            nibble = 0;
        }
    }
    return octets == 6 && nibble == 0;
}

/* ble_addr_t is little-endian: val[0] is the LAST octet of the printed form. */
static bool ble_mac_matches_config(const uint8_t mac[6], const ble_addr_t *addr)
{
    for (int i = 0; i < 6; ++i) {
        if (mac[i] != addr->val[5 - i]) {
            return false;
        }
    }
    return true;
}

static void ble_format_addr(const ble_addr_t *addr, char *out, size_t cap)
{
    (void)snprintf(out, cap, "%02X:%02X:%02X:%02X:%02X:%02X", addr->val[5], addr->val[4],
                   addr->val[3], addr->val[2], addr->val[1], addr->val[0]);
}

static bool ble_addr_equals(const ble_addr_t *a, const ble_addr_t *b)
{
    return memcmp(a->val, b->val, sizeof(a->val)) == 0;
}

static bool ble_addr_is_zero(const ble_addr_t *addr)
{
    for (int i = 0; i < 6; ++i) {
        if (addr->val[i] != 0) {
            return false;
        }
    }
    return true;
}

/* Case-insensitive ASCII substring (mirrors the Python `needle in candidate`). */
static bool ble_contains_ci(const char *haystack, const char *needle)
{
    size_t needle_len;

    if (haystack == NULL || needle == NULL) {
        return false;
    }
    needle_len = strlen(needle);
    if (needle_len == 0) {
        return false;
    }
    for (const char *p = haystack; *p != '\0'; ++p) {
        size_t i = 0;
        while (i < needle_len && p[i] != '\0' &&
               ble_lower_ascii(p[i]) == ble_lower_ascii(needle[i])) {
            i += 1;
        }
        if (i == needle_len) {
            return true;
        }
    }
    return false;
}

/* --------------------------------------------------------- advisement scan */

/* Console list: deduplicate by address and keep the strongest RSSI / best name. */
static void ble_scan_list_record(const struct ble_gap_disc_desc *desc, const char *name,
                                 size_t name_len)
{
    ble_list_entry_t *entry = NULL;

    if (name_len >= BLE_SCAN_LIST_NAME_LEN) {
        name_len = BLE_SCAN_LIST_NAME_LEN - 1;
    }
    /* A report without a name is still useful (it can be configured by address),
     * but one with neither a name nor an address identifies nothing. */
    if (name_len == 0 && ble_addr_is_zero(&desc->addr)) {
        return;
    }

    for (int i = 0; i < s_list_count; ++i) {
        if (ble_addr_equals(&s_list[i].addr, &desc->addr)) {
            entry = &s_list[i];
            break;
        }
    }
    if (entry == NULL) {
        if (s_list_count >= s_list_limit) {
            return;
        }
        entry = &s_list[s_list_count];
        memset(entry, 0, sizeof(*entry));
        entry->addr = desc->addr;
        s_list_count += 1;
    }

    if (desc->rssi != 127 && (!entry->has_rssi || desc->rssi > entry->rssi)) {
        entry->has_rssi = true;
        entry->rssi = desc->rssi;
    }
    if (name_len > 0) {
        if (entry->name[0] == '\0' || name_len > strlen(entry->name)) {
            memcpy(entry->name, name, name_len);
            entry->name[name_len] = '\0';
        }
    }
}

static void ble_scan_on_advertisement(const struct ble_gap_disc_desc *desc)
{
    struct ble_hs_adv_fields fields;
    bool parsed = false;
    char name[BLE_ADV_NAME_MAX];
    size_t name_len = 0;

    name[0] = '\0';
    if (desc->data != NULL && desc->length_data > 0) {
        parsed = ble_hs_adv_parse_fields(&fields, desc->data, desc->length_data) == 0;
    }
    if (parsed && fields.name != NULL && fields.name_len > 0) {
        name_len = fields.name_len;
        if (name_len > sizeof(name) - 1) {
            name_len = sizeof(name) - 1;
        }
        memcpy(name, fields.name, name_len);
        name[name_len] = '\0';
    }

    if (s_ble.list_mode) {
        ble_scan_list_record(desc, name, name_len);
        return;
    }

    for (int i = 0; i < s_ble.scan_count; ++i) {
        ble_scan_slot_t *slot = &s_scan[i];
        bool match = slot->found && ble_addr_equals(&slot->addr, &desc->addr);

        if (!slot->found) {
            if (s_match[i].has_mac) {
                match = ble_mac_matches_config(s_match[i].mac, &desc->addr);
            } else if (s_match[i].has_name_filter && name_len > 0) {
                match = ble_contains_ci(name, s_match[i].name_filter);
            }
        }
        if (!match) {
            continue;
        }
        if (!slot->found) {
            slot->found = true;
            slot->addr = desc->addr;
        }
        /* 127 dBm is the HCI "RSSI not available" sentinel. */
        slot->has_rssi = desc->rssi != 127;
        slot->rssi = desc->rssi;
        if (name_len > 0) {
            memcpy(slot->name, name, name_len + 1);
            slot->has_name = true;
        }
    }
}

/* ------------------------------------------------------------ GAP handling */

static int ble_gap_event_handler(struct ble_gap_event *event, void *arg)
{
    (void)arg;

    switch (event->type) {
    case BLE_GAP_EVENT_DISC:
        if (s_ble.scanning) {
            ble_scan_on_advertisement(&event->disc);
        }
        return 0;

    case BLE_GAP_EVENT_DISC_COMPLETE:
        s_ble.scanning = false;
        ble_set_event(EV_SCAN_DONE);
        return 0;

    case BLE_GAP_EVENT_CONNECT:
        s_ble.connect_status = event->connect.status;
        if (event->connect.status == 0) {
            s_ble.conn_handle = event->connect.conn_handle;
            s_ble.connected = true;
        }
        ble_set_event(EV_CONNECT);
        return 0;

    case BLE_GAP_EVENT_DISCONNECT:
        s_ble.disconnect_reason = event->disconnect.reason;
        s_ble.connected = false;
        s_ble.conn_handle = BLE_HS_CONN_HANDLE_NONE;
        ble_set_event(EV_LINK_DOWN);
        return 0;

    case BLE_GAP_EVENT_NOTIFY_RX: {
        if (event->notify_rx.conn_handle == s_ble.conn_handle &&
            event->notify_rx.attr_handle == s_ble.chr_val && !s_ble.hr_received &&
            event->notify_rx.om != NULL) {
            uint16_t pkt_len = OS_MBUF_PKTLEN(event->notify_rx.om);
            size_t copy_len = pkt_len;
            if (copy_len > sizeof(s_ble.hr_buf)) {
                copy_len = sizeof(s_ble.hr_buf);
            }
            if (copy_len > 0 &&
                os_mbuf_copydata(event->notify_rx.om, 0, (int)copy_len, s_ble.hr_buf) == 0) {
                s_ble.hr_len = (int)copy_len;
                s_ble.hr_received = true;
                ble_set_event(EV_HR_NOTIFY);
            }
        }
        return 0;
    }

    default:
        return 0;
    }
}

/* ----------------------------------------------------------- GATT callbacks */

static int ble_svc_discovery_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                                const struct ble_gatt_svc *service, void *arg)
{
    (void)conn_handle;
    (void)arg;

    if (error->status == 0 && service != NULL) {
        s_ble.svc_start = service->start_handle;
        s_ble.svc_end = service->end_handle;
        s_ble.svc_found = true;
        return 0;
    }
    /* Any other status (ENOENT / EDONE / ...) means the procedure is over. */
    ble_set_event(EV_SVC_DONE);
    return 0;
}

static int ble_chr_discovery_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                                const struct ble_gatt_chr *chr, void *arg)
{
    (void)conn_handle;
    (void)arg;

    if (error->status == 0 && chr != NULL) {
        if (!s_ble.chr_found) {
            s_ble.chr_val = chr->val_handle;
            s_ble.chr_found = true;
        }
        return 0;
    }
    ble_set_event(EV_CHR_DONE);
    return 0;
}

static int ble_dsc_discovery_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                                uint16_t chr_val_handle, const struct ble_gatt_dsc *dsc,
                                void *arg)
{
    (void)conn_handle;
    (void)chr_val_handle;
    (void)arg;

    if (error->status == 0 && dsc != NULL) {
        /* The CCCD is the first descriptor after the characteristic value.  Its
         * UUID is not inspected on purpose: the handler stays valid regardless
         * of whether NimBLE stores UUIDs by value or by pointer. */
        if (!s_ble.dsc_found && dsc->handle > s_ble.chr_val) {
            s_ble.dsc_handle = dsc->handle;
            s_ble.dsc_found = true;
        }
        return 0;
    }
    ble_set_event(EV_DSC_DONE);
    return 0;
}

static int ble_read_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                       struct ble_gatt_attr *attr, void *arg)
{
    ble_read_target_t *target = (ble_read_target_t *)arg;

    (void)conn_handle;

    if (error->status == 0 && attr != NULL && attr->om != NULL && target != NULL) {
        uint16_t pkt_len = OS_MBUF_PKTLEN(attr->om);
        size_t copy_len = pkt_len;
        if (copy_len > target->cap) {
            copy_len = target->cap;
        }
        if (copy_len > 0 &&
            os_mbuf_copydata(attr->om, 0, (int)copy_len, target->buf) == 0) {
            target->len = (int)copy_len;
        }
    }
    s_ble.read_status = error->status;
    ble_set_event(EV_READ_DONE);
    return 0;
}

static int ble_write_cb(uint16_t conn_handle, const struct ble_gatt_error *error,
                        struct ble_gatt_attr *attr, void *arg)
{
    (void)conn_handle;
    (void)attr;
    (void)arg;

    s_ble.write_status = error->status;
    ble_set_event(EV_WRITE_DONE);
    return 0;
}

/* ------------------------------------------------------------ GATT helpers */

static bool ble_gatt_find_service(uint16_t uuid16, uint16_t *start, uint16_t *end)
{
    int rc;

    s_ble.svc_found = false;
    s_ble.svc_start = 0;
    s_ble.svc_end = 0;
    ble_clear_events(EV_SVC_DONE);

    rc = ble_gattc_disc_svc_by_uuid(s_ble.conn_handle, BLE_UUID16_DECLARE(uuid16),
                                    ble_svc_discovery_cb, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "service discovery 0x%04X could not start; rc=%d", uuid16, rc);
        return false;
    }
    if (!ble_wait_for(EV_SVC_DONE, BLE_GATT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "service discovery 0x%04X timed out", uuid16);
        s_ble.gatt_stalled = true;
        return false;
    }
    if (!s_ble.svc_found) {
        return false;
    }
    *start = s_ble.svc_start;
    *end = s_ble.svc_end;
    return true;
}

static bool ble_gatt_find_chr(uint16_t start, uint16_t end, uint16_t uuid16, uint16_t *val)
{
    int rc;

    s_ble.chr_found = false;
    s_ble.chr_val = 0;
    ble_clear_events(EV_CHR_DONE);

    rc = ble_gattc_disc_chrs_by_uuid(s_ble.conn_handle, start, end, BLE_UUID16_DECLARE(uuid16),
                                     ble_chr_discovery_cb, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "characteristic discovery 0x%04X could not start; rc=%d", uuid16, rc);
        return false;
    }
    if (!ble_wait_for(EV_CHR_DONE, BLE_GATT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "characteristic discovery 0x%04X timed out", uuid16);
        s_ble.gatt_stalled = true;
        return false;
    }
    if (!s_ble.chr_found) {
        return false;
    }
    *val = s_ble.chr_val;
    return true;
}

static bool ble_gatt_find_cccd(uint16_t chr_val, uint16_t end, uint16_t *handle)
{
    int rc;

    s_ble.dsc_found = false;
    s_ble.dsc_handle = 0;
    ble_clear_events(EV_DSC_DONE);

    rc = ble_gattc_disc_all_dscs(s_ble.conn_handle, chr_val, end, ble_dsc_discovery_cb, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "descriptor discovery could not start; rc=%d", rc);
        return false;
    }
    if (!ble_wait_for(EV_DSC_DONE, BLE_GATT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "descriptor discovery timed out");
        s_ble.gatt_stalled = true;
        return false;
    }
    if (!s_ble.dsc_found) {
        return false;
    }
    *handle = s_ble.dsc_handle;
    return true;
}

static bool ble_gatt_read_value(uint16_t attr_handle, ble_read_target_t *target)
{
    int rc;

    s_ble.read_status = -1;
    ble_clear_events(EV_READ_DONE);

    rc = ble_gattc_read(s_ble.conn_handle, attr_handle, ble_read_cb, target);
    if (rc != 0) {
        ESP_LOGW(TAG, "characteristic read could not start; rc=%d", rc);
        return false;
    }
    if (!ble_wait_for(EV_READ_DONE, BLE_GATT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "characteristic read timed out");
        s_ble.gatt_stalled = true;
        return false;
    }
    return s_ble.read_status == 0;
}

static bool ble_gatt_enable_notifications(uint16_t cccd_handle)
{
    static const uint8_t k_enable[2] = { 0x01, 0x00 };
    int rc;

    s_ble.write_status = -1;
    ble_clear_events(EV_WRITE_DONE);

    rc = ble_gattc_write_flat(s_ble.conn_handle, cccd_handle, k_enable, sizeof(k_enable),
                              ble_write_cb, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "CCCD write could not start; rc=%d", rc);
        return false;
    }
    if (!ble_wait_for(EV_WRITE_DONE, BLE_GATT_TIMEOUT_MS)) {
        ESP_LOGW(TAG, "CCCD write timed out");
        s_ble.gatt_stalled = true;
        return false;
    }
    return s_ble.write_status == 0;
}

/* Heart Rate Measurement: flags bit 0 selects uint8 vs little-endian uint16. */
static bool ble_parse_heart_rate(const uint8_t *data, int len, int32_t *out)
{
    if (data == NULL || len < 2) {
        return false;
    }
    if ((data[0] & 0x01u) != 0) {
        if (len < 3) {
            return false;
        }
        *out = (int32_t)((uint16_t)data[1] | ((uint16_t)data[2] << 8));
    } else {
        *out = (int32_t)data[1];
    }
    return true;
}

/* ------------------------------------------------------ connect / readings */

static bool ble_connect(const ble_addr_t *addr, int32_t timeout_ms)
{
    uint8_t own_addr_type = 0;
    int rc;

    if (timeout_ms < 1) {
        timeout_ms = 1;
    }
    s_ble.connect_status = -1;
    s_ble.connected = false;
    s_ble.conn_handle = BLE_HS_CONN_HANDLE_NONE;
    s_ble.gatt_stalled = false;
    s_ble.chr_found = false;
    s_ble.chr_val = 0;
    s_ble.hr_received = false;
    s_ble.hr_len = 0;
    ble_clear_events(EV_CONNECT | EV_LINK_DOWN | EV_SVC_DONE | EV_CHR_DONE | EV_DSC_DONE |
                     EV_READ_DONE | EV_WRITE_DONE | EV_HR_NOTIFY);

    rc = ble_hs_id_infer_auto(0, &own_addr_type);
    if (rc != 0) {
        ESP_LOGW(TAG, "cannot determine the local BLE address type; rc=%d", rc);
        return false;
    }
    rc = ble_gap_connect(own_addr_type, addr, timeout_ms, NULL, ble_gap_event_handler, NULL);
    if (rc != 0) {
        ESP_LOGW(TAG, "ble_gap_connect failed; rc=%d", rc);
        return false;
    }
    if (!ble_wait_for(EV_CONNECT, (uint32_t)timeout_ms + 1000u)) {
        ESP_LOGW(TAG, "connection attempt timed out");
        return false;
    }
    if (s_ble.connect_status != 0) {
        ESP_LOGW(TAG, "connection failed; status=%d", s_ble.connect_status);
        return false;
    }
    return s_ble.connected;
}

static void ble_disconnect(void)
{
    int rc;

    if (!s_ble.connected || s_ble.conn_handle == BLE_HS_CONN_HANDLE_NONE) {
        return;
    }
    ble_clear_events(EV_LINK_DOWN);
    rc = ble_gap_terminate(s_ble.conn_handle, BLE_ERR_REM_USER_CONN_TERM);
    if (rc != 0) {
        ESP_LOGW(TAG, "ble_gap_terminate failed; rc=%d", rc);
        return;
    }
    (void)ble_wait_for(EV_LINK_DOWN, BLE_DISCONNECT_WAIT_MS);
}

static void ble_read_battery(ble_reading_t *out)
{
    uint16_t start = 0;
    uint16_t end = 0;
    uint16_t val = 0;
    uint8_t value[BLE_BATTERY_READ_MAX];
    ble_read_target_t target = {
        .buf = value,
        .cap = sizeof(value),
        .len = 0,
    };

    if (!ble_gatt_find_service(BLE_SVC_BATTERY_UUID, &start, &end)) {
        return;
    }
    if (!ble_gatt_find_chr(start, end, BLE_CHR_BATTERY_LEVEL_UUID, &val)) {
        return;
    }
    if (!ble_gatt_read_value(val, &target)) {
        return;
    }
    /* 0x2A19 is a single percentage byte; anything outside 0..100 is invalid. */
    if (target.len >= 1 && value[0] <= 100) {
        out->has_battery = true;
        out->battery = (int32_t)value[0];
    }
}

static void ble_read_heart_rate(const sc_ble_cfg_t *item, int64_t deadline_ms, ble_reading_t *out)
{
    uint16_t start = 0;
    uint16_t end = 0;
    uint16_t val = 0;
    uint16_t cccd = 0;
    uint32_t wait_ms;
    int64_t remaining_ms;
    int32_t bpm = 0;

    if (!ble_gatt_find_service(BLE_SVC_HEART_RATE_UUID, &start, &end)) {
        return;
    }
    if (!ble_gatt_find_chr(start, end, BLE_CHR_HEART_RATE_MEASUREMENT_UUID, &val)) {
        return;
    }
    s_ble.chr_val = val;
    if (!ble_gatt_find_cccd(val, end, &cccd)) {
        return;
    }

    /* Clear the notification slot before subscribing: a measurement may arrive
     * immediately after the CCCD write is confirmed. */
    s_ble.hr_received = false;
    s_ble.hr_len = 0;
    ble_clear_events(EV_HR_NOTIFY);
    if (!ble_gatt_enable_notifications(cccd)) {
        return;
    }

    wait_ms = (uint32_t)item->heart_rate_wait_seconds * 1000u;
    if (wait_ms == 0u) {
        wait_ms = (uint32_t)SC_DEFAULT_HR_WAIT_SECONDS * 1000u;
    }
    if (wait_ms > 8000u) {
        wait_ms = 8000u;
    }
    /* Never wait past the round budget. */
    remaining_ms = deadline_ms - ble_now_ms();
    if (remaining_ms <= 0) {
        return;
    }
    if ((int64_t)wait_ms > remaining_ms) {
        wait_ms = (uint32_t)remaining_ms;
    }
    if (!s_ble.hr_received) {
        (void)ble_wait_for(EV_HR_NOTIFY, wait_ms);
    }
    if (s_ble.hr_received && ble_parse_heart_rate(s_ble.hr_buf, s_ble.hr_len, &bpm)) {
        out->has_heart_rate = true;
        out->heart_rate = bpm;
    }
}

static void ble_read_device(const sc_ble_cfg_t *item, const ble_scan_slot_t *slot,
                            int64_t deadline_ms, ble_reading_t *out)
{
    int64_t now_ms = ble_now_ms();
    int32_t connect_ms = BLE_CONNECT_TIMEOUT_MS;

    memset(out, 0, sizeof(*out));

    /* Keep the connection attempt inside the round budget. */
    if (deadline_ms > now_ms) {
        int64_t remaining_ms = deadline_ms - now_ms;
        if (remaining_ms < (int64_t)connect_ms) {
            connect_ms = (int32_t)remaining_ms;
        }
    } else {
        connect_ms = 1;
    }

    if (!ble_connect(&slot->addr, connect_ms)) {
        return;
    }
    out->linked = true;

    if (ble_now_ms() >= deadline_ms) {
        ESP_LOGW(TAG, "round budget spent after connect; skipping GATT reads");
        ble_disconnect();
        return;
    }

    /* The Python collector reads battery first, then subscribes to heart rate. */
    if (item->want_battery && !s_ble.gatt_stalled && s_ble.connected) {
        ble_read_battery(out);
    }
    if (item->want_heart_rate && !s_ble.gatt_stalled && s_ble.connected &&
        ble_now_ms() < deadline_ms) {
        ble_read_heart_rate(item, deadline_ms, out);
    }
    ble_disconnect();
}

/* ------------------------------------------------------------ row builders */

static void ble_fill_base(sc_device_t *row, const sc_config_t *cfg, const sc_ble_cfg_t *item,
                          int64_t now)
{
    char display_name[SC_STR_LEN];

    sc_str_set_raw(&row->id, item->id.v);
    sc_str_set_raw(&row->platform, sc_platform_token(SC_PLATFORM_BLE));
    sc_str_set_raw(&row->model, item->name.v);
    sc_str_set_raw(&row->display_name,
                   sc_display_name(&cfg->aliases, item->name.v, display_name,
                                   sizeof(display_name)));
    sc_str_set_raw(&row->media_state, "unknown");
    row->updated_at.has = true;
    row->updated_at.v = now;

    row->has_transport = true;
    sc_str_set_raw(&row->transport.active, "ble");
    row->transport.degraded.has = true;
    row->transport.degraded.v = false;
    row->transport.online.has = true;
    row->transport.online.v = false;

    row->has_ble = true;
    row->ble.connected.has = true;
    row->ble.connected.v = false;
    if (item->want_battery) {
        (void)sc_strlist_add(&row->ble.services, "battery");
    }
    if (item->want_heart_rate) {
        (void)sc_strlist_add(&row->ble.services, "heart_rate");
    }
}

static void ble_fill_not_found(sc_device_t *row, const sc_ble_cfg_t *item)
{
    if (item->address.has) {
        char upper[SC_STR_LEN];
        ble_copy_upper(upper, sizeof(upper), item->address.v);
        sc_str_set_raw(&row->ble.address, upper);
    }
    sc_str_set_raw(&row->ble.status_text, BLE_STATUS_NOT_FOUND);
    /* transport.detail is free-form; the firmware uses a fixed reason token. */
    sc_str_set_raw(&row->transport.detail, "not found");
}

static void ble_fill_found(sc_device_t *row, const sc_ble_cfg_t *item,
                           const ble_scan_slot_t *slot, const ble_reading_t *reading, int64_t now)
{
    char addr[18];
    bool degraded;

    ble_format_addr(&slot->addr, addr, sizeof(addr));
    sc_str_set_raw(&row->ble.address, addr);

    if (slot->has_name) {
        sc_str_set_raw(&row->ble.name, slot->name);
    } else {
        /* bleak falls back to the BLEDevice name and finally the config name. */
        sc_str_set_raw(&row->ble.name, item->name.v);
    }
    if (slot->has_rssi) {
        row->ble.rssi.has = true;
        row->ble.rssi.v = slot->rssi;
    }

    row->ble.connected.v = reading->linked;
    row->ble.last_seen_at.has = true;
    row->ble.last_seen_at.v = now;

    row->transport.online.v = true;
    row->transport.last_seen_at.has = true;
    row->transport.last_seen_at.v = now;

    if (reading->linked) {
        if (reading->has_battery) {
            row->battery_level.has = true;
            row->battery_level.v = reading->battery;
        }
        if (reading->has_heart_rate) {
            row->ble.heart_rate.has = true;
            row->ble.heart_rate.v = reading->heart_rate;
        }
        /* A connected peripheral whose requested value was not delivered still
         * proves liveness, but not the full payload: mark the channel degraded
         * (same rule as the Python collector). */
        degraded = (item->want_battery && !reading->has_battery) ||
                   (item->want_heart_rate && !reading->has_heart_rate);
    } else {
        degraded = true;
        sc_str_set_raw(&row->ble.status_text, BLE_STATUS_NOT_CONNECTED);
    }
    row->transport.degraded.v = degraded;
}

/* ------------------------------------------------------------- scan driver */

/* Start one discovery procedure, collect advertisements for the shared window
 * and stop it again.  The caller selects what the GAP callback records by
 * setting s_ble.list_mode / s_ble.scan_count beforehand. */
static bool ble_scan_window(void)
{
    struct ble_gap_disc_params params = { 0 };
    uint8_t own_addr_type = 0;
    int rc;

    rc = ble_hs_id_infer_auto(0, &own_addr_type);
    if (rc != 0) {
        ESP_LOGW(TAG, "cannot determine the local BLE address type; rc=%d", rc);
        return false;
    }

    /* Active scan (the names usually live in the scan response) and no
     * duplicate filtering so RSSI and the scan-response name both land here. */
    params.passive = 0;
    params.filter_duplicates = 0;
    params.limited = 0;
    params.filter_policy = 0;

    ble_clear_events(EV_SCAN_DONE);
    s_ble.scanning = true;
    rc = ble_gap_disc(own_addr_type, BLE_HS_FOREVER, &params, ble_gap_event_handler, NULL);
    if (rc != 0) {
        s_ble.scanning = false;
        ESP_LOGW(TAG, "ble_gap_disc failed; rc=%d", rc);
        return false;
    }

    /* Let the controller report for the shared scan window, then stop it. */
    (void)xEventGroupWaitBits(s_ble.events, EV_SCAN_DONE, pdFALSE, pdFALSE,
                              (TickType_t)pdMS_TO_TICKS(BLE_SCAN_WINDOW_MS));
    if (s_ble.scanning) {
        rc = ble_gap_disc_cancel();
        if (rc != 0) {
            ESP_LOGW(TAG, "ble_gap_disc_cancel failed; rc=%d", rc);
        }
        (void)xEventGroupWaitBits(s_ble.events, EV_SCAN_DONE, pdFALSE, pdFALSE,
                                  (TickType_t)pdMS_TO_TICKS(BLE_SCAN_STOP_MS));
    }
    s_ble.scanning = false;
    ble_clear_events(EV_SCAN_DONE);
    return true;
}

static bool ble_scan(const sc_config_t *cfg, int count)
{
    int seen = 0;

    memset(s_scan, 0, sizeof(s_scan));
    memset(s_match, 0, sizeof(s_match));
    s_ble.scan_count = count;

    for (int i = 0; i < count; ++i) {
        const sc_ble_cfg_t *item = &cfg->ble[i];

        if (item->address.has) {
            s_match[i].has_mac = ble_parse_mac(item->address.v, s_match[i].mac);
        }
        if (!s_match[i].has_mac && item->name_filter.has) {
            size_t len = strlen(item->name_filter.v);
            if (len > sizeof(s_match[i].name_filter) - 1) {
                len = sizeof(s_match[i].name_filter) - 1;
            }
            memcpy(s_match[i].name_filter, item->name_filter.v, len);
            s_match[i].name_filter[len] = '\0';
            s_match[i].has_name_filter = true;
        }
    }

    s_ble.list_mode = false;
    if (!ble_scan_window()) {
        return false;
    }

    for (int i = 0; i < count; ++i) {
        if (s_scan[i].found) {
            seen += 1;
        }
    }
    ESP_LOGI(TAG, "scan finished: %d/%d configured device(s) seen", seen, count);
    return true;
}

/* -------------------------------------------------------------- NimBLE init */

static void ble_host_task(void *param)
{
    (void)param;
    ESP_LOGI(TAG, "NimBLE host task started");
    nimble_port_run();
    nimble_port_freertos_deinit();
}

static void ble_on_sync(void)
{
    int rc = ble_hs_util_ensure_addr(0);

    if (rc != 0) {
        ESP_LOGE(TAG, "ble_hs_util_ensure_addr failed; rc=%d", rc);
        return;
    }
    s_ble.synced = true;
    ble_set_event(EV_SYNC);
    ESP_LOGI(TAG, "NimBLE host synchronised");
}

static void ble_on_reset(int reason)
{
    s_ble.synced = false;
    ESP_LOGE(TAG, "NimBLE host reset; reason=%d", reason);
}

void ble_collector_init(void)
{
    esp_err_t err;

    if (s_ble.init_attempted) {
        return;
    }
    memset(&s_ble, 0, sizeof(s_ble));
    s_ble.init_attempted = true;
    s_ble.conn_handle = BLE_HS_CONN_HANDLE_NONE;

    s_ble.events = xEventGroupCreate();
    if (s_ble.events == NULL) {
        ESP_LOGE(TAG, "cannot create the BLE event group");
        return;
    }
    s_ble.lock = xSemaphoreCreateMutex();
    if (s_ble.lock == NULL) {
        ESP_LOGE(TAG, "cannot create the BLE mutex");
        return;
    }

    /* app_main() normally initialises NVS first; this keeps the collector
     * self-contained when it is embedded elsewhere. */
    err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS needs to be erased before Bluetooth can start");
        if (nvs_flash_erase() == ESP_OK) {
            err = nvs_flash_init();
        }
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nvs_flash_init failed; 0x%x", err);
        return;
    }

    /* IDF 5.x: nimble_port_init() brings up the controller and the host. */
    err = nimble_port_init();
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init failed; 0x%x", err);
        return;
    }
    ble_hs_cfg.reset_cb = ble_on_reset;
    ble_hs_cfg.sync_cb = ble_on_sync;

    nimble_port_freertos_init(ble_host_task);

    if (!ble_wait_for(EV_SYNC, BLE_SYNC_WAIT_MS)) {
        ESP_LOGW(TAG, "NimBLE host did not sync within %d ms", BLE_SYNC_WAIT_MS);
    }
    s_ble.inited = true;
}

/* -------------------------------------------------------------- public API */

int ble_collector_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now)
{
    int count;
    int appended = 0;
    int64_t deadline_ms;

    if (cfg == NULL || snapshot == NULL) {
        return 0;
    }
    count = cfg->ble_count;
    if (count <= 0) {
        return 0;
    }
    if (count > SC_MAX_BLE_DEVICES) {
        count = SC_MAX_BLE_DEVICES;
    }
    if (!s_ble.inited || !s_ble.synced || s_ble.events == NULL || s_ble.lock == NULL) {
        ESP_LOGW(TAG, "NimBLE host is not ready; skipping the BLE round");
        return 0;
    }
    /* The console scan uses the same scan state and event bits. */
    if (xSemaphoreTake(s_ble.lock, portMAX_DELAY) != pdTRUE) {
        ESP_LOGW(TAG, "cannot lock the BLE state; skipping the BLE round");
        return 0;
    }

    if (!ble_scan(cfg, count)) {
        /* A failed scan is reported as "not seen", never as a row error. */
        ESP_LOGW(TAG, "BLE scan could not be started; every device is reported as 未发现");
    }

    /* The scan has its own 4 s cap; everything after it shares this budget so a
     * round stays inside the 30 s refresh interval. */
    deadline_ms = ble_now_ms() + BLE_ROUND_BUDGET_MS;

    for (int i = 0; i < count; ++i) {
        sc_device_t *row = sc_snapshot_append(snapshot);
        if (row == NULL) {
            ESP_LOGW(TAG, "snapshot is full; %d BLE row(s) dropped", count - i);
            break;
        }
        appended += 1;

        const sc_ble_cfg_t *item = &cfg->ble[i];
        ble_fill_base(row, cfg, item, now);

        if (!s_scan[i].found) {
            ble_fill_not_found(row, item);
            ESP_LOGI(TAG, "ble %s: not found", item->id.v);
            continue;
        }

        ble_reading_t reading;
        if (ble_now_ms() >= deadline_ms) {
            /* Out of budget: the device was still seen, so it keeps the
             * "seen but not connected" shape instead of being probed. */
            memset(&reading, 0, sizeof(reading));
            ESP_LOGW(TAG, "ble %s: round budget spent; not connecting", item->id.v);
        } else {
            ble_read_device(item, &s_scan[i], deadline_ms, &reading);
        }
        ble_fill_found(row, item, &s_scan[i], &reading, now);
        ESP_LOGI(TAG, "ble %s: connected=%d battery=%d heart_rate=%d", item->id.v,
                 reading.linked ? 1 : 0, reading.has_battery ? (int)reading.battery : -1,
                 reading.has_heart_rate ? (int)reading.heart_rate : -1);
    }
    /* A connection attempt that timed out can still complete late; never leave
     * a link behind when the round ends. */
    ble_disconnect();
    (void)xSemaphoreGive(s_ble.lock);
    return appended;
}

int ble_collector_scan_list(int max, char names[][32], char addrs[][18], int8_t *rssi)
{
    bool scanned;
    int written = 0;

    if (max <= 0 || names == NULL || addrs == NULL || rssi == NULL) {
        return 0;
    }
    if (!s_ble.inited || !s_ble.synced || s_ble.events == NULL || s_ble.lock == NULL) {
        ESP_LOGW(TAG, "NimBLE host is not ready; scan list unavailable");
        return 0;
    }
    /* Never interleave with a collector round; report "busy" instead of waiting
     * because a round can hold the lock for many seconds. */
    if (xSemaphoreTake(s_ble.lock, 0) != pdTRUE) {
        ESP_LOGW(TAG, "BLE round in progress; scan list skipped");
        return 0;
    }

    memset(s_list, 0, sizeof(s_list));
    s_list_count = 0;
    s_list_limit = (max < BLE_SCAN_LIST_MAX) ? max : BLE_SCAN_LIST_MAX;
    s_ble.list_mode = true;
    scanned = ble_scan_window();
    s_ble.list_mode = false;

    if (scanned) {
        written = (s_list_count < max) ? s_list_count : max;
        for (int i = 0; i < written; ++i) {
            const ble_list_entry_t *entry = &s_list[i];

            (void)snprintf(names[i], BLE_SCAN_LIST_NAME_LEN, "%s", entry->name);
            /* The console (like the display) is ASCII-only. */
            for (char *p = names[i]; *p != '\0'; ++p) {
                unsigned char c = (unsigned char)*p;
                if (c < 0x20u || c > 0x7Eu) {
                    *p = '?';
                }
            }
            ble_format_addr(&entry->addr, addrs[i], BLE_SCAN_LIST_ADDR_LEN);
            rssi[i] = entry->has_rssi ? (int8_t)entry->rssi : (int8_t)BLE_RSSI_UNKNOWN;
        }
        ESP_LOGI(TAG, "scan list: %d device(s)", written);
    }

    (void)xSemaphoreGive(s_ble.lock);
    return written;
}
