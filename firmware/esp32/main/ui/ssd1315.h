/*
 * ssd1315 -- minimal I2C driver for the 128x64 SSD1315 OLED (SSD1306 command
 * set) on the ESP-IDF 5.x new I2C master driver.
 *
 * The panel is optional hardware: until ssd1315_init() has found and prepared a
 * panel, ssd1315_ready() is false and every other entry point is a no-op. The
 * driver owns an 8-page x 128-column framebuffer and only pushes it when
 * ssd1315_flush() is called; there is no background refresh task here.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "driver/i2c_master.h"
#include "esp_err.h"

/* Create the I2C bus, probe `i2c_addr` (falling back to the other of 0x3C /
 * 0x3D), send the init sequence and blank the panel. On a missing panel it
 * logs once and returns an error; a later call does not recreate the bus. */
esp_err_t ssd1315_init(int sda_gpio, int scl_gpio, uint8_t i2c_addr);

/* True once the panel answered and the init sequence completed. */
bool ssd1315_ready(void);

/* Zero the whole framebuffer (call ssd1315_flush() to push it). */
void ssd1315_clear(void);

/* Draw 6x8 text starting at pixel column `x` on 8-pixel page `page` (0..7).
 * Characters outside 0x20..0x7E render as '?'. With `invert` the text cell is
 * drawn as black-on-white, which is what the page indicator uses.
 * Returns the x cursor just past the last character. */
int ssd1315_draw_text(int x, int page, const char *text, bool invert);

/* Draw 8x16 text starting at pixel column `x`. `page` is a page pair 0..3 and
 * occupies pages page*2 and page*2+1 (so the glyph spans 16 pixel rows).
 * Returns the x cursor just past the last character. */
int ssd1315_draw_text_large(int x, int page, const char *text);

/* Set or clear one pixel of the framebuffer. Out-of-range coordinates are
 * ignored. */
void ssd1315_draw_pixel(int x, int y, bool on);

/* Probe every 7-bit I2C address on the driver's bus and fill up to `max`
 * entries. Returns the number of devices that answered (0 when the bus was never
 * created). Used by `i2c scan` to verify extra sensors on the shared bus. */
int ssd1315_i2c_scan(uint8_t *addrs, int max);

/* The I2C bus the panel lives on (NULL before init). Other devices on the same
 * bus -- e.g. a DHT20 at 0x38 -- use this instead of creating a second bus. */
i2c_master_bus_handle_t ssd1315_bus_handle(void);

/* Push the framebuffer to the panel. No-op when the panel is not ready. */
void ssd1315_flush(void);
