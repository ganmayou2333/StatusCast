/*
 * sc_led -- the status light's colour table and its auto/manual policy.
 *
 * The RMT plumbing lives in ui/status_led.c; everything decision-shaped lives
 * here so it can be tested without a board. That split is not academic: the
 * manual-override path used to report "off" while the pixel was lit, and turning
 * the override off left the pixel stuck on the manual colour whenever the
 * automatic state happened to still be OFF (the painter skipped the repaint
 * because its cached state had not changed). Both were invisible to every test
 * because the whole decision lived inside an ESP-IDF-dependent file.
 *
 * Two ideas are kept apart on purpose:
 *
 *   auto_state    what the automatic machine last asked for
 *   manual_state  the override colour, when one is in force
 *   shown         which of the two the pixel is actually displaying
 *
 * "off" therefore means "the pixel is dark", never "automatic control is
 * suspended" -- that second question is answered by `mode`.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

typedef enum {
    SC_LED_OFF = 0,   /* dark */
    SC_LED_OK,        /* green */
    SC_LED_DEGRADED,  /* amber */
    SC_LED_ERROR,     /* red */
} sc_led_state_t;

/* One palette for both paths, so "green" means the same thing whichever way it
 * was chosen. Brightness is deliberately low: this sits on a desk. */
void sc_led_rgb(sc_led_state_t state, uint8_t *red, uint8_t *green, uint8_t *blue);

const char *sc_led_name(sc_led_state_t state);

/* Parses the four colour names POST /control/led accepts. False for anything
 * else, so the caller can answer 400 without duplicating the list. */
bool sc_led_state_from_name(const char *name, sc_led_state_t *out);

typedef struct {
    bool available;              /* false when the strip failed to initialise */
    bool manual;                 /* an override is in force */
    sc_led_state_t auto_state;   /* what the automatic machine last asked for */
    sc_led_state_t manual_state; /* the override colour */
} sc_led_model_t;

void sc_led_model_init(sc_led_model_t *model, bool available);

/* Both setters return true when the pixel needs repainting, i.e. when the
 * displayed colour actually changed. That is the only condition a caller has to
 * respect, and it is why turning an override off always restores the automatic
 * colour instead of depending on a later state transition. */
bool sc_led_model_set_auto(sc_led_model_t *model, sc_led_state_t state);
bool sc_led_model_set_manual(sc_led_model_t *model, bool enabled, sc_led_state_t state);

/* The colour the pixel is showing right now. */
sc_led_state_t sc_led_model_shown(const sc_led_model_t *model);

/* "auto" while the automatic machine is in control, "manual" during an override. */
const char *sc_led_model_mode_name(const sc_led_model_t *model);
