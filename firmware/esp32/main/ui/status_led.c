#include "status_led.h"

#include "esp_log.h"
#include "led_strip.h"

static const char *TAG = "sc_led";

#define STATUS_LED_GPIO 48

static led_strip_handle_t s_strip;
static sc_led_model_t s_model;

/* Paint whatever the model says is showing. No-op without a strip. */
static void paint(void)
{
    if (s_strip == NULL) {
        return;
    }
    uint8_t red = 0;
    uint8_t green = 0;
    uint8_t blue = 0;
    sc_led_rgb(sc_led_model_shown(&s_model), &red, &green, &blue);
    (void)led_strip_set_pixel(s_strip, 0, red, green, blue);
    (void)led_strip_refresh(s_strip);
}

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
        sc_led_model_init(&s_model, false);
        return;
    }
    (void)led_strip_clear(s_strip);
    sc_led_model_init(&s_model, true);
}

void status_led_set(sc_led_state_t state)
{
    if (sc_led_model_set_auto(&s_model, state)) {
        paint();
    }
}

void status_led_set_manual(bool enabled, sc_led_state_t state)
{
    if (sc_led_model_set_manual(&s_model, enabled, state)) {
        paint();
    }
}

bool status_led_manual_active(void)
{
    return s_model.manual;
}

sc_led_state_t status_led_shown(void)
{
    return sc_led_model_shown(&s_model);
}

const char *status_led_mode_name(void)
{
    return sc_led_model_mode_name(&s_model);
}

bool status_led_available(void)
{
    return s_model.available;
}
