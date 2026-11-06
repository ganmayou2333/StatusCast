#include "ui.h"

#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "app_state.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#include "sc_display.h"
#include "ssd1315.h"

/* Sensible fallbacks so the file also builds when sdkconfig.h is regenerated
 * before Kconfig.projbuild is picked up. */
#ifndef CONFIG_SC_I2C_SDA_GPIO
#define CONFIG_SC_I2C_SDA_GPIO 8
#endif
#ifndef CONFIG_SC_I2C_SCL_GPIO
#define CONFIG_SC_I2C_SCL_GPIO 9
#endif
#ifndef CONFIG_SC_OLED_I2C_ADDR
#define CONFIG_SC_OLED_I2C_ADDR 0x3C
#endif
#ifndef CONFIG_SC_OLED_CAROUSEL_SECONDS
#define CONFIG_SC_OLED_CAROUSEL_SECONDS 5
#endif

#define UI_TASK_STACK 4096
#define UI_TASK_PRIORITY 3
#define UI_TASK_CORE 1
#define UI_PANEL_WIDTH 128

static const char *TAG = "sc_ui";

#if CONFIG_SC_ENABLE_DISPLAY

/* app_state.c exports this, but app_state.h does not declare it yet. Declaring
 * it here keeps this module's header untouched; the definition lives in
 * app_state.c and a later declaration in app_state.h stays compatible. */
extern int64_t app_state_generated_at(void);

static TaskHandle_t s_task;
static bool s_started;

static sc_display_model_t s_model;
static sc_display_model_t s_next_model;
static int s_page;
static int64_t s_last_poll_us;
static int64_t s_last_switch_us;
static int64_t s_last_generated;
static bool s_have_model;
static app_sensor_t s_sensor;
static app_sensor_t s_last_sensor;

/* --------------------------------------------------------------- helpers */

static int page_count(const sc_display_model_t *model)
{
    int devices = model->row_count;
    if (devices < 0) {
        devices = 0;
    }
    if (devices > SC_DISPLAY_ROWS) {
        devices = SC_DISPLAY_ROWS;
    }
    /* Overview page plus one page per device; with no devices a single
     * "no devices" page takes the place of the per-device pages. A working
     * DHT20 adds one more page at the end. */
    int pages = 1 + (devices > 0 ? devices : 1);
    if (s_sensor.ok) {
        pages += 1;
    }
    return pages;
}

static bool model_equal(const sc_display_model_t *left, const sc_display_model_t *right)
{
    if (left->device_count != right->device_count || left->row_count != right->row_count) {
        return false;
    }
    for (int i = 0; i < left->row_count; ++i) {
        const sc_display_row_t *a = &left->rows[i];
        const sc_display_row_t *b = &right->rows[i];
        if (a->icon != b->icon || a->online != b->online || a->degraded != b->degraded) {
            return false;
        }
        if (strcmp(a->title, b->title) != 0 || strcmp(a->status, b->status) != 0 ||
            strcmp(a->detail, b->detail) != 0) {
            return false;
        }
    }
    return true;
}

/* Copy the published display model and report whether anything changed since
 * the last poll (new snapshot generation or different row contents). */
static bool poll_state(void)
{
    (void)app_state_copy_display(&s_next_model);
    int64_t generated = app_state_generated_at();
    s_sensor = app_state_sensor();
    bool sensor_changed = s_sensor.ok != s_last_sensor.ok ||
                          (s_sensor.ok && (s_sensor.temperature_c != s_last_sensor.temperature_c ||
                                           s_sensor.humidity_pct != s_last_sensor.humidity_pct));
    s_last_sensor = s_sensor;
    bool changed = !s_have_model || generated != s_last_generated ||
                   !model_equal(&s_next_model, &s_model) || sensor_changed;
    s_model = s_next_model;
    s_last_generated = generated;
    s_have_model = true;
    return changed;
}

/* ---------------------------------------------------------------- drawing */

static void draw_overview(const sc_display_model_t *model)
{
    app_network_t net = app_state_network();
    char line[64];

    if (net.wifi_connected && net.ip[0] != '\0') {
        /* A line holds 21 cells, so "StatusCast " plus a full IPv4 address
         * (up to 15 characters) does not fit. Keep the address readable by
         * shortening the brand only when the long form would be clipped. */
        if (strlen("StatusCast ") + strlen(net.ip) <= SC_DISPLAY_TEXT_LEN) {
            (void)snprintf(line, sizeof(line), "StatusCast %s", net.ip);
        } else {
            (void)snprintf(line, sizeof(line), "SC %s", net.ip);
        }
    } else {
        (void)snprintf(line, sizeof(line), "StatusCast no wifi");
    }
    ssd1315_draw_text(0, 0, line, false);

    const char *hostname = (net.hostname[0] != '\0') ? net.hostname : "-";
    (void)snprintf(line, sizeof(line), "%s %ddBm", hostname, net.rssi);
    ssd1315_draw_text(0, 1, line, false);

    char summary[32];
    sc_display_summary(model, summary, sizeof(summary));
    /* Rows with a phone/windows icon can only have come from the PC feed. */
    bool pc_sourced = false;
    for (int i = 0; i < model->row_count; ++i) {
        char icon = model->rows[i].icon;
        if (icon == 'P' || icon == 'A' || icon == 'W') {
            pc_sourced = true;
            break;
        }
    }
    if (pc_sourced) {
        (void)snprintf(line, sizeof(line), "%s pc:on", summary);
    } else {
        (void)snprintf(line, sizeof(line), "%s", summary);
    }
    ssd1315_draw_text(0, 2, line, false);

    int64_t uptime_us = esp_timer_get_time();
    int64_t uptime_s = uptime_us / 1000000;
    int days = (int)(uptime_s / 86400);
    int hours = (int)((uptime_s / 3600) % 24);
    int minutes = (int)((uptime_s / 60) % 60);

    int64_t now = app_now_unix();
    if (app_time_valid() && now > 0) {
        int seconds_today = (int)(now % 86400);
        (void)snprintf(line, sizeof(line), "up %dd %02d:%02d %02d:%02d:%02d", days, hours,
                       minutes, seconds_today / 3600, (seconds_today / 60) % 60,
                       seconds_today % 60);
    } else {
        (void)snprintf(line, sizeof(line), "up %dd %02d:%02d", days, hours, minutes);
    }
    ssd1315_draw_text(0, 3, line, false);

    if (s_sensor.ok) {
        (void)snprintf(line, sizeof(line), "T %.1fC H %.0f%%", s_sensor.temperature_c,
                       s_sensor.humidity_pct);
        ssd1315_draw_text(0, 4, line, false);
    }
}

static void draw_sensor_page(const sc_display_model_t *model)
{
    char line[32];
    int pages = page_count(model);

    ssd1315_draw_text_large(0, 0, "DHT20");
    (void)snprintf(line, sizeof(line), "%.1f C", s_sensor.temperature_c);
    ssd1315_draw_text(0, 2, line, false);
    (void)snprintf(line, sizeof(line), "%.1f %%RH", s_sensor.humidity_pct);
    ssd1315_draw_text(0, 3, line, false);
    ssd1315_draw_text(0, 4, "indoor", false);

    (void)snprintf(line, sizeof(line), "%d/%d", pages, pages);
    int width = (int)strlen(line) * 6;
    ssd1315_draw_text(width < UI_PANEL_WIDTH ? UI_PANEL_WIDTH - width : 0, 7, line, true);
}

static void draw_device_page(const sc_display_model_t *model, int page)
{
    if (model->row_count <= 0) {
        /* Placeholder page when there is nothing to carousel through. */
        const char *empty = "no devices";
        int width = (int)strlen(empty) * 6;
        int x = (UI_PANEL_WIDTH - width) / 2;
        ssd1315_draw_text(x < 0 ? 0 : x, 3, empty, false);
        return;
    }

    int index = page - 1;
    if (index < 0) {
        index = 0;
    }
    if (index >= model->row_count) {
        index = model->row_count - 1;
    }
    const sc_display_row_t *row = &model->rows[index];
    char line[64];

    /* Row 0: large title across the two top pages (8x16, 16 characters). */
    ssd1315_draw_text_large(0, 0, row->title);

    /* Row 2: platform icon plus battery / online state (6x8). */
    if (row->status[0] != '\0') {
        (void)snprintf(line, sizeof(line), "%c %s", row->icon, row->status);
    } else {
        (void)snprintf(line, sizeof(line), "%c", row->icon);
    }
    ssd1315_draw_text(0, 2, line, false);

    /* Row 3: detail (method / media / game), clipped by the driver. */
    ssd1315_draw_text(0, 3, row->detail, false);

    /* Bottom-right page indicator, inverted so it reads as a badge. */
    char indicator[32];
    (void)snprintf(indicator, sizeof(indicator), "%d/%d", index + 1, model->row_count);
    int width = (int)strlen(indicator) * 6;
    int x = UI_PANEL_WIDTH - width;
    ssd1315_draw_text(x < 0 ? 0 : x, 7, indicator, true);
}

static void redraw(int page, const sc_display_model_t *model)
{
    ssd1315_clear();
    int device_pages = model->row_count > 0 ? model->row_count : 1;
    if (page <= 0) {
        draw_overview(model);
    } else if (page <= device_pages) {
        draw_device_page(model, page);
    } else {
        draw_sensor_page(model);
    }
    ssd1315_flush();
}

/* ------------------------------------------------------------------ task */

static void ui_task(void *arg)
{
    (void)arg;

    esp_err_t err = ssd1315_init(CONFIG_SC_I2C_SDA_GPIO, CONFIG_SC_I2C_SCL_GPIO,
                                 (uint8_t)CONFIG_SC_OLED_I2C_ADDR);
    if (err != ESP_OK) {
        /* The panel is optional: keep the task alive so a later re-init (for
         * example from a console command) is picked up on the next loop. */
        ESP_LOGW(TAG, "display init failed: %s", esp_err_to_name(err));
    }

    int64_t carousel_us = (int64_t)CONFIG_SC_OLED_CAROUSEL_SECONDS * 1000000;
    if (carousel_us <= 0) {
        carousel_us = 5 * 1000000;
    }

    bool was_ready = false;
    bool idle_logged = false;

    for (;;) {
        if (!ssd1315_ready()) {
            if (!idle_logged) {
                ESP_LOGI(TAG, "display unavailable; UI idle");
                idle_logged = true;
            }
            was_ready = false;
            vTaskDelay(pdMS_TO_TICKS(1000));
            continue;
        }

        int64_t now_us = esp_timer_get_time();
        bool dirty = false;

        if (!was_ready) {
            /* The panel just became usable: start from the overview page. */
            was_ready = true;
            idle_logged = false;
            s_have_model = false;
            s_last_poll_us = 0;
            s_last_switch_us = now_us;
            s_page = 0;
            dirty = true;
        }

        /* Poll at 1 Hz only; the panel is redrawn on change, not on a timer. */
        if (s_last_poll_us == 0 || now_us - s_last_poll_us >= 1000000) {
            s_last_poll_us = now_us;
            if (poll_state()) {
                dirty = true;
            }
        }

        int pages = page_count(&s_model);
        if (s_page >= pages) {
            /* The device count shrank while we were parked on a higher page. */
            s_page = 0;
            dirty = true;
        }

        if (now_us - s_last_switch_us >= carousel_us) {
            s_last_switch_us = now_us;
            /* Also refresh the model on the forced page change. */
            if (poll_state()) {
                dirty = true;
            }
            pages = page_count(&s_model);
            if (s_page >= pages) {
                s_page = 0;
            }
            s_page = (s_page + 1) % pages;
            dirty = true;
        }

        if (dirty) {
            pages = page_count(&s_model);
            if (s_page >= pages) {
                s_page = 0;
            }
            redraw(s_page, &s_model);
        }

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

#endif /* CONFIG_SC_ENABLE_DISPLAY */

void ui_start(void)
{
#if CONFIG_SC_ENABLE_DISPLAY
    if (s_started) {
        return;
    }
    s_started = true;
    if (xTaskCreatePinnedToCore(ui_task, "sc_ui", UI_TASK_STACK, NULL, UI_TASK_PRIORITY,
                                &s_task, UI_TASK_CORE) != pdPASS) {
        s_task = NULL;
        ESP_LOGE(TAG, "failed to create the UI task");
    }
#else
    ESP_LOGI(TAG, "display support disabled at build time");
#endif
}
