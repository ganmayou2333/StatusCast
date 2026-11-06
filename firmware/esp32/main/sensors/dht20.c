#include "dht20.h"

#include <string.h>

#include "driver/i2c_master.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "ui/ssd1315.h"

static const char *TAG = "sc_dht20";

#define DHT20_ADDRESS 0x38
#define DHT20_TIMEOUT_MS 120
#define DHT20_MEASURE_DELAY_MS 90
#define DHT20_RAW_SCALE 1048576.0f /* 2^20 */

static i2c_master_dev_handle_t s_device;

static esp_err_t ensure_device(void)
{
    if (s_device != NULL) {
        return ESP_OK;
    }
    i2c_master_bus_handle_t bus = ssd1315_bus_handle();
    if (bus == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    const i2c_device_config_t config = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = DHT20_ADDRESS,
        .scl_speed_hz = 100000,
    };
    esp_err_t err = i2c_master_bus_add_device(bus, &config, &s_device);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "cannot attach DHT20: %s", esp_err_to_name(err));
    }
    return err;
}

esp_err_t dht20_read(dht20_reading_t *out)
{
    if (out == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t err = ensure_device();
    if (err != ESP_OK) {
        return err;
    }

    const uint8_t command[3] = {0xAC, 0x33, 0x00};
    err = i2c_master_transmit(s_device, command, sizeof(command), DHT20_TIMEOUT_MS);
    if (err != ESP_OK) {
        return err;
    }
    vTaskDelay(pdMS_TO_TICKS(DHT20_MEASURE_DELAY_MS));

    uint8_t data[7] = {0};
    err = i2c_master_receive(s_device, data, sizeof(data), DHT20_TIMEOUT_MS);
    if (err != ESP_OK) {
        return err;
    }
    if ((data[0] & 0x80) != 0) {
        /* Still busy; the caller can try again next round. */
        return ESP_ERR_TIMEOUT;
    }

    const uint32_t raw_humidity =
        (((uint32_t)data[1] << 16) | ((uint32_t)data[2] << 8) | (uint32_t)data[3]) >> 4;
    const uint32_t raw_temperature =
        (((uint32_t)data[3] & 0x0FU) << 16) | ((uint32_t)data[4] << 8) | (uint32_t)data[5];

    out->humidity_pct = (float)raw_humidity * 100.0f / DHT20_RAW_SCALE;
    out->temperature_c = (float)raw_temperature * 200.0f / DHT20_RAW_SCALE - 50.0f;
    return ESP_OK;
}
