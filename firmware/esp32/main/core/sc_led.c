#include "sc_led.h"

#include <stddef.h>
#include <string.h>

/* 0..255. The strip is a desk indicator, not a lamp. */
#define SC_LED_BRIGHTNESS 24

void sc_led_rgb(sc_led_state_t state, uint8_t *red, uint8_t *green, uint8_t *blue)
{
    uint8_t r = 0;
    uint8_t g = 0;
    uint8_t b = 0;
    switch (state) {
    case SC_LED_OK:
        g = SC_LED_BRIGHTNESS;
        break;
    case SC_LED_DEGRADED:
        r = SC_LED_BRIGHTNESS / 2;
        g = SC_LED_BRIGHTNESS;
        break;
    case SC_LED_ERROR:
        r = SC_LED_BRIGHTNESS;
        break;
    case SC_LED_OFF:
    default:
        break;
    }
    if (red != NULL) {
        *red = r;
    }
    if (green != NULL) {
        *green = g;
    }
    if (blue != NULL) {
        *blue = b;
    }
}

const char *sc_led_name(sc_led_state_t state)
{
    switch (state) {
    case SC_LED_OK:
        return "green";
    case SC_LED_DEGRADED:
        return "amber";
    case SC_LED_ERROR:
        return "red";
    case SC_LED_OFF:
    default:
        return "off";
    }
}

bool sc_led_state_from_name(const char *name, sc_led_state_t *out)
{
    if (name == NULL || out == NULL) {
        return false;
    }
    static const struct {
        const char *name;
        sc_led_state_t state;
    } table[] = {
        {"green", SC_LED_OK},
        {"amber", SC_LED_DEGRADED},
        {"red", SC_LED_ERROR},
        {"off", SC_LED_OFF},
    };
    for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); ++i) {
        if (strcmp(table[i].name, name) == 0) {
            *out = table[i].state;
            return true;
        }
    }
    return false;
}

static sc_led_state_t shown_of(const sc_led_model_t *model)
{
    return model->manual ? model->manual_state : model->auto_state;
}

void sc_led_model_init(sc_led_model_t *model, bool available)
{
    if (model == NULL) {
        return;
    }
    model->available = available;
    model->manual = false;
    model->auto_state = SC_LED_OFF;
    model->manual_state = SC_LED_OFF;
}

bool sc_led_model_set_auto(sc_led_model_t *model, sc_led_state_t state)
{
    if (model == NULL) {
        return false;
    }
    sc_led_state_t before = shown_of(model);
    model->auto_state = state;
    return shown_of(model) != before;
}

bool sc_led_model_set_manual(sc_led_model_t *model, bool enabled, sc_led_state_t state)
{
    if (model == NULL) {
        return false;
    }
    sc_led_state_t before = shown_of(model);
    model->manual = enabled;
    if (enabled) {
        model->manual_state = state;
    }
    /* Turning the override off reveals auto_state again -- including when it is
     * OFF, which is exactly the case that used to leave the pixel stuck. */
    return shown_of(model) != before;
}

sc_led_state_t sc_led_model_shown(const sc_led_model_t *model)
{
    if (model == NULL) {
        return SC_LED_OFF;
    }
    return shown_of(model);
}

const char *sc_led_model_mode_name(const sc_led_model_t *model)
{
    return (model != NULL && model->manual) ? "manual" : "auto";
}
