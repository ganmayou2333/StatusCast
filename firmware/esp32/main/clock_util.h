/*
 * clock_util -- the two clocks and the watchdog feed, for ESP-IDF-side files.
 *
 * Deliberately outside core/: the host test build compiles main/core/ with a
 * plain C compiler, so anything touching esp_timer or the task watchdog has to
 * stay on this side of the line.
 *
 * Two different clocks are in play and mixing them is a bug:
 *   sc_mono_ms()   monotonic milliseconds since boot -- deadlines and budgets
 *   app_now_unix() wall-clock seconds -- snapshot timestamps
 */
#pragma once

#include <stdint.h>

#include "esp_task_wdt.h"
#include "esp_timer.h"

/* Monotonic milliseconds. Never jumps, safe to subtract. */
static inline int64_t sc_mono_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* Tell the task watchdog this task is still making progress.
 *
 * Only meaningful after the calling task has subscribed with
 * esp_task_wdt_add(). Returns the watchdog's error code so a caller that cares
 * can tell "not subscribed" from "subscribed and fed"; most callers ignore it.
 */
static inline esp_err_t sc_wdt_feed(void)
{
    return esp_task_wdt_reset();
}
