/*
 * ui -- the display task for the optional 128x64 SSD1315 OLED.
 *
 * One task owns the panel. It shows an overview page followed by one page per
 * device and advances the carousel every CONFIG_SC_OLED_CAROUSEL_SECONDS. The
 * snapshot is polled at 1 Hz (and on every page change) but the panel is only
 * redrawn when the snapshot generation or the current page actually changed,
 * so the I2C bus stays quiet between updates.
 */
#pragma once

/* Create the display task once; later calls are no-ops. */
void ui_start(void);
