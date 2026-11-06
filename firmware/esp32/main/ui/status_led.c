#include "status_led.h"

#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "sc_led";

#define STATUS_LED_GPIO 48
#define STATUS_LED_BRIGHTNESS 24 /* 0..255: this sits on a desk, keep it dim */

static led_strip_handle_t s_strip;
static status_led_state_t s_state;
static bool s_manual;

void status_led_init(void)
{
    if (s_strip != NULL) {
        return;
    }
    const led_strip_config_t strip_config = {
        .strip_gpio_num = STATUS_LED_GPIO,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags.invert_out = false,
    };
    const led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10 * 1000 * 1000,
        .mem_block_symbols = 64,
        .flags.with_dma = false,
    };
    esp_err_t err = led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "status LED unavailable: %s", esp_err_to_name(err));
        s_strip = NULL;
        return;
    }
    (void)led_strip_clear(s_strip);
    s_state = STATUS_LED_OFF;
}

void status_led_set_manual(bool enabled, unsigned red, unsigned green, unsigned blue)
{
    s_manual = enabled;
    if (s_strip == NULL) {
        return;
    }
    if (!enabled) {
        return;
    }
    (void)led_strip_set_pixel(s_strip, 0, red, green, blue);
    (void)led_strip_refresh(s_strip);
    s_state = STATUS_LED_OFF;
}

bool status_led_manual_active(void)
{
    return s_manual;
}

void status_led_set(status_led_state_t state)
{
    if (s_strip == NULL || state == s_state || s_manual) {
        return;
    }
    uint8_t red = 0;
    uint8_t green = 0;
    switch (state) {
    case STATUS_LED_OK:
        green = STATUS_LED_BRIGHTNESS;
        break;
    case STATUS_LED_DEGRADED:
        red = STATUS_LED_BRIGHTNESS / 2;
        green = STATUS_LED_BRIGHTNESS;
        break;
    case STATUS_LED_ERROR:
        red = STATUS_LED_BRIGHTNESS;
        break;
    case STATUS_LED_OFF:
    default:
        break;
    }
    (void)led_strip_set_pixel(s_strip, 0, red, green, 0);
    (void)led_strip_refresh(s_strip);
    s_state = state;
}

status_led_state_t status_led_get(void)
{
    return s_state;
}

const char *status_led_name(status_led_state_t state)
{
    switch (state) {
    case STATUS_LED_OK:
        return "green";
    case STATUS_LED_DEGRADED:
        return "amber";
    case STATUS_LED_ERROR:
        return "red";
    case STATUS_LED_OFF:
    default:
        return "off";
    }
}
