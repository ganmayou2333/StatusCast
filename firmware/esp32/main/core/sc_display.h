/*
 * sc_display -- turn a snapshot into render-ready ASCII rows for the 128x64 OLED.
 *
 * The panel only has an ASCII font (no Chinese glyphs), so this module is where
 * the "non-ASCII becomes '?'" and "never show an empty row" rules live. Keeping
 * it in core means the host tests can pin those rules without a board.
 */
#pragma once

#include <stddef.h>

#include "sc_core.h"

#define SC_DISPLAY_ROWS SC_MAX_DEVICES
#define SC_DISPLAY_TEXT_LEN 21

typedef struct {
    char icon;                        /* G=console P=ios A=android B=ble W=windows */
    char title[SC_DISPLAY_TEXT_LEN];  /* device name, ASCII only */
    char status[SC_DISPLAY_TEXT_LEN]; /* battery / online state, ASCII only */
    char detail[SC_DISPLAY_TEXT_LEN]; /* method / media / game, ASCII only */
    bool online;
    bool degraded;
} sc_display_row_t;

typedef struct {
    int device_count;
    int row_count;
    sc_display_row_t rows[SC_DISPLAY_ROWS];
} sc_display_model_t;

/* Copy `src` into `dst` keeping only printable ASCII; every non-ASCII code
 * point becomes '?', control characters are dropped. Always NUL terminates. */
void sc_display_ascii(const char *src, char *dst, size_t cap);

/* Build the per-device rows. Order follows the snapshot order so the carousel is
 * stable between rounds. */
void sc_display_build(const sc_snapshot_t *snapshot, sc_display_model_t *out);

/* One-line summary for the overview page, e.g. "5 devices  3 online". */
void sc_display_summary(const sc_display_model_t *model, char *out, size_t cap);
