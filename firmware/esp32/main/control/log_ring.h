/*
 * log_ring -- keep the last N log lines in PSRAM so they can be fetched over
 * HTTP (GET /control/log).
 *
 * It hooks esp_log_set_vprintf and forwards every line to the previous sink, so
 * the UART output is unchanged. Storage is 128 fixed lines in PSRAM (the ring is
 * line-based, which makes dumping chronological and bounded). The hook never
 * blocks: if another task holds the lock the line is simply forwarded and not
 * retained.
 */
#pragma once

#include <stddef.h>

void log_ring_init(void);
/* Copy up to `max_lines` of the newest lines (chronological order) into `dst` as
 * plain text, one line per '\n'. Returns the number of bytes written. */
size_t log_ring_dump(char *dst, size_t cap, int max_lines);
