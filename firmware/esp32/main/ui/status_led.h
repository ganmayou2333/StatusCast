/*
 * status_led -- the onboard WS2812 on GPIO48 acts as a one-pixel status light.
 *
 *   green  every device is online and the PC feed is fresh
 *   amber  something is offline or degraded (still serving)
 *   red    WiFi down, or the PC feed is stale (rows rewritten as 读取超时)
 *   off    nothing to report yet
 *
 * This file is only the painter: the colour table and the auto/manual policy
 * live in core/sc_led.c so they can be tested without a board. `status_led_shown`
 * and `status_led_mode_name` answer "what is the pixel doing" and "who decided
 * it" separately -- reporting them as one value is what made /diag claim
 * `"led":"off"` while an override had the pixel lit.
 *
 * The LED is optional hardware: if the RMT/strip setup fails every call becomes a
 * no-op, `status_led_available()` returns false, and the rest of the firmware
 * keeps working.
 */
#pragma once

#include <stdbool.h>

#include "sc_led.h"

void status_led_init(void);

/* Automatic state machine. Ignored while a manual override is in force, but the
 * requested state is remembered so handing control back restores it at once. */
void status_led_set(sc_led_state_t state);

/* Manual override using the same palette. `enabled = false` hands control back
 * to the automatic machine and repaints immediately. */
void status_led_set_manual(bool enabled, sc_led_state_t state);

bool status_led_manual_active(void);

/* What the strip is showing right now, and who chose it ("auto" / "manual"). */
sc_led_state_t status_led_shown(void);
const char *status_led_mode_name(void);

/* False when the strip could not be initialised (the firmware still runs). */
bool status_led_available(void);
