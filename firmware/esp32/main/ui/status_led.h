/*
 * status_led -- the onboard WS2812 on GPIO48 acts as a one-pixel status light.
 *
 *   green  every device is online and the PC feed is fresh
 *   amber  something is offline or degraded (still serving)
 *   red    WiFi down, or the PC feed is stale (rows rewritten as 读取超时)
 *   off    nothing to report yet
 *
 * The LED is optional hardware: if the RMT/strip setup fails every call becomes a
 * no-op so the rest of the firmware keeps working.
 */
#pragma once

typedef enum {
    STATUS_LED_OFF = 0,
    STATUS_LED_OK,
    STATUS_LED_DEGRADED,
    STATUS_LED_ERROR,
} status_led_state_t;

void status_led_init(void);
void status_led_set(status_led_state_t state);
status_led_state_t status_led_get(void);
const char *status_led_name(status_led_state_t state);
