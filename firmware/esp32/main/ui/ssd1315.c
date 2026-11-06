#include "ssd1315.h"

#include <string.h>

#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "font6x8.h"
#include "font8x16.h"

#define SSD1315_I2C_PORT I2C_NUM_0
#define SSD1315_I2C_FREQ_HZ 400000
#define SSD1315_I2C_TIMEOUT_MS 100

#define SSD1315_WIDTH 128
#define SSD1315_HEIGHT 64
#define SSD1315_PAGES (SSD1315_HEIGHT / 8)

#define SSD1315_CTRL_CMD 0x00
#define SSD1315_CTRL_DATA 0x40

#define SSD1315_FIRST_CHAR 0x20
#define SSD1315_LAST_CHAR 0x7E
#define SSD1315_PLACEHOLDER '?'

/* Largest payload byte count we ever put behind one control byte. Keeping it at
 * 128 means a whole page of pixels fits in one I2C transaction. */
#define SSD1315_MAX_PAYLOAD 128

static const char *TAG = "ssd1315";

/* SSD1315 uses the SSD1306 command set. Sequence per the panel datasheet:
 * clock divider, multiplex, offset, start line, charge pump, horizontal
 * addressing, segment/COM direction, COM pins, contrast, precharge, VCOMH,
 * then resume/normal/display-on. */
static const uint8_t k_init_sequence[] = {
    0xAE,       /* display off */
    0xD5, 0x80, /* clock divide ratio */
    0xA8, 0x3F, /* multiplex ratio: 64 rows */
    0xD3, 0x00, /* display offset 0 */
    0x40,       /* start line 0 */
    0x8D, 0x14, /* charge pump on */
    0x20, 0x00, /* memory mode: horizontal */
    0xA1,       /* segment remap */
    0xC8,       /* COM scan direction: remapped */
    0xDA, 0x12, /* COM pins: alternative, no left/right remap */
    0x81, 0xCF, /* contrast */
    0xD9, 0xF1, /* precharge period */
    0xDB, 0x40, /* VCOMH deselect level */
    0xA4,       /* resume from RAM */
    0xA6,       /* normal (not inverted) display */
    0xAF,       /* display on */
};

static i2c_master_bus_handle_t s_bus;
static i2c_master_dev_handle_t s_dev;
static bool s_ready;
static bool s_bus_created;
static bool s_failure_logged;
static uint8_t s_addr;

static uint8_t s_fb[SSD1315_PAGES][SSD1315_WIDTH];

/* ------------------------------------------------------------------ helpers */

static esp_err_t i2c_write(uint8_t control, const uint8_t *payload, size_t length)
{
    if (s_dev == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (payload == NULL || length == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (length > SSD1315_MAX_PAYLOAD) {
        length = SSD1315_MAX_PAYLOAD;
    }
    uint8_t buffer[1 + SSD1315_MAX_PAYLOAD];
    buffer[0] = control;
    memcpy(&buffer[1], payload, length);
    return i2c_master_transmit(s_dev, buffer, length + 1, SSD1315_I2C_TIMEOUT_MS);
}

static esp_err_t i2c_write_commands(const uint8_t *commands, size_t length)
{
    return i2c_write(SSD1315_CTRL_CMD, commands, length);
}

static esp_err_t probe_address(uint16_t address)
{
    return i2c_master_probe(s_bus, address, SSD1315_I2C_TIMEOUT_MS);
}

/* Probes every 7-bit address on the same bus the panel uses. Used by the
 * `i2c scan` console command to verify a second device (e.g. a DHT20 at 0x38)
 * without guessing. */
int ssd1315_i2c_scan(uint8_t *addrs, int max)
{
    if (s_bus == NULL || addrs == NULL || max <= 0) {
        return 0;
    }
    int found = 0;
    for (uint16_t address = 0x08; address <= 0x77 && found < max; ++address) {
        /* Probe twice: a floating bus with long jumper wires can fake a single
         * ACK, a real device answers consistently. */
        if (i2c_master_probe(s_bus, address, 30) == ESP_OK &&
            i2c_master_probe(s_bus, address, 30) == ESP_OK) {
            addrs[found++] = (uint8_t)address;
        }
    }
    return found;
}

static void log_failure_once(esp_err_t err, const char *what)
{
    if (s_failure_logged) {
        return;
    }
    ESP_LOGE(TAG, "%s: %s", what, esp_err_to_name(err));
    s_failure_logged = true;
}

static uint8_t sanitize_char(unsigned char c)
{
    if (c < SSD1315_FIRST_CHAR || c > SSD1315_LAST_CHAR) {
        return (uint8_t)SSD1315_PLACEHOLDER;
    }
    return (uint8_t)c;
}

/* ------------------------------------------------------------------- public */

i2c_master_bus_handle_t ssd1315_bus_handle(void)
{
    return s_bus;
}

esp_err_t ssd1315_init(int sda_gpio, int scl_gpio, uint8_t i2c_addr)
{
    if (s_ready) {
        return ESP_OK;
    }
    if (sda_gpio < 0 || scl_gpio < 0) {
        ESP_LOGE(TAG, "invalid I2C pins sda=%d scl=%d", sda_gpio, scl_gpio);
        return ESP_ERR_INVALID_ARG;
    }

    /* The bus is created at most once. A missing panel only fails the probe,
     * so a later init call can retry on the same bus without recreating it. */
    if (!s_bus_created) {
        i2c_master_bus_config_t bus_config = {
            .i2c_port = SSD1315_I2C_PORT,
            .sda_io_num = (gpio_num_t)sda_gpio,
            .scl_io_num = (gpio_num_t)scl_gpio,
            .clk_source = I2C_CLK_SRC_DEFAULT,
            .glitch_ignore_cnt = 7,
            .intr_priority = 0,
            .trans_queue_depth = 0,
            .flags = {
                .enable_internal_pullup = true,
                .allow_pd = false,
            },
        };
        esp_err_t err = i2c_new_master_bus(&bus_config, &s_bus);
        if (err != ESP_OK) {
            s_bus = NULL;
            log_failure_once(err, "i2c bus init failed");
            return err;
        }
        s_bus_created = true;
    }

    /* Try the configured address first, then the other of 0x3C / 0x3D. */
    uint8_t candidate = i2c_addr;
    uint8_t alternate = (i2c_addr == 0x3D) ? 0x3C : 0x3D;
    esp_err_t err = probe_address(candidate);
    if (err != ESP_OK) {
        candidate = alternate;
        err = probe_address(candidate);
    }
    if (err != ESP_OK) {
        /* Absent panel is not fatal: remember the failure and let the caller
         * carry on. A retry is allowed but only logs once. */
        if (!s_failure_logged) {
            ESP_LOGE(TAG, "no SSD1315 at 0x%02X or 0x%02X; display disabled",
                     (unsigned)i2c_addr, (unsigned)alternate);
            s_failure_logged = true;
        }
        return ESP_ERR_NOT_FOUND;
    }

    if (s_dev != NULL) {
        (void)i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
    }
    i2c_device_config_t dev_config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = candidate,
        .scl_speed_hz = SSD1315_I2C_FREQ_HZ,
        .scl_wait_us = 0,
        .flags = {
            .disable_ack_check = false,
        },
    };
    err = i2c_master_bus_add_device(s_bus, &dev_config, &s_dev);
    if (err != ESP_OK) {
        s_dev = NULL;
        log_failure_once(err, "i2c add device failed");
        return err;
    }
    s_addr = candidate;

    err = i2c_write_commands(k_init_sequence, sizeof(k_init_sequence));
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "SSD1315 init sequence failed at 0x%02X: %s", (unsigned)s_addr,
                 esp_err_to_name(err));
        (void)i2c_master_bus_rm_device(s_dev);
        s_dev = NULL;
        return err;
    }
    /* The charge pump needs a moment before the first frame is readable. */
    vTaskDelay(pdMS_TO_TICKS(100));

    s_ready = true;
    s_failure_logged = false;
    ESP_LOGI(TAG, "SSD1315 ready at 0x%02X (sda=%d scl=%d)", (unsigned)s_addr, sda_gpio,
             scl_gpio);

    /* Display RAM contents are undefined after power-up, so blank it once. */
    ssd1315_clear();
    ssd1315_flush();
    return ESP_OK;
}

bool ssd1315_ready(void)
{
    return s_ready;
}

void ssd1315_clear(void)
{
    if (!s_ready) {
        return;
    }
    memset(s_fb, 0, sizeof(s_fb));
}

int ssd1315_draw_text(int x, int page, const char *text, bool invert)
{
    if (!s_ready || text == NULL || page < 0 || page >= SSD1315_PAGES) {
        return x;
    }

    int cursor = x;
    for (const unsigned char *p = (const unsigned char *)text; *p != 0; ++p) {
        uint8_t code = sanitize_char(*p);
        const uint8_t *glyph = &sc_font6x8_bits[code - SSD1315_FIRST_CHAR][0][0];
        for (int column = 0; column < 6; ++column) {
            int pixel = cursor + column;
            if (pixel < 0 || pixel >= SSD1315_WIDTH) {
                continue;
            }
            uint8_t bits = glyph[column];
            if (invert) {
                /* Filled cell with the glyph knocked out. */
                s_fb[page][pixel] = (uint8_t)~bits;
            } else {
                s_fb[page][pixel] |= bits;
            }
        }
        cursor += 6;
    }
    return cursor;
}

int ssd1315_draw_text_large(int x, int page, const char *text)
{
    if (!s_ready || text == NULL || page < 0 || page > 3) {
        return x;
    }

    const int top_page = page * 2;
    int cursor = x;
    for (const unsigned char *p = (const unsigned char *)text; *p != 0; ++p) {
        uint8_t code = sanitize_char(*p);
        const uint8_t *glyph = &sc_font8x16_bits[code - SSD1315_FIRST_CHAR][0][0];
        for (int column = 0; column < 8; ++column) {
            int pixel = cursor + column;
            if (pixel < 0 || pixel >= SSD1315_WIDTH) {
                continue;
            }
            /* Font layout is [char][column][page]: one byte per 8-row page. */
            s_fb[top_page][pixel] |= glyph[column * 2];
            s_fb[top_page + 1][pixel] |= glyph[column * 2 + 1];
        }
        cursor += 8;
    }
    return cursor;
}

void ssd1315_draw_pixel(int x, int y, bool on)
{
    if (!s_ready || x < 0 || x >= SSD1315_WIDTH || y < 0 || y >= SSD1315_HEIGHT) {
        return;
    }
    uint8_t mask = (uint8_t)(1u << (y & 7));
    if (on) {
        s_fb[y >> 3][x] |= mask;
    } else {
        s_fb[y >> 3][x] &= (uint8_t)~mask;
    }
}

void ssd1315_flush(void)
{
    if (!s_ready || s_dev == NULL) {
        return;
    }

    /* Address the whole screen once, then stream one page per transaction. */
    static const uint8_t window[] = {
        0x21, 0x00, 0x7F, /* column start/end */
        0x22, 0x00, 0x07, /* page start/end */
    };
    esp_err_t err = i2c_write_commands(window, sizeof(window));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "window setup failed: %s", esp_err_to_name(err));
        return;
    }

    for (int page = 0; page < SSD1315_PAGES; ++page) {
        uint8_t buffer[1 + SSD1315_WIDTH];
        buffer[0] = SSD1315_CTRL_DATA;
        memcpy(&buffer[1], s_fb[page], SSD1315_WIDTH);
        err = i2c_master_transmit(s_dev, buffer, sizeof(buffer), SSD1315_I2C_TIMEOUT_MS);
        if (err != ESP_OK) {
            ESP_LOGW(TAG, "frame page %d failed: %s", page, esp_err_to_name(err));
            return;
        }
    }
}
