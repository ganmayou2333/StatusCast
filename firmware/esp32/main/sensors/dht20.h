/*
 * dht20 -- temperature/humidity sensor on the OLED's I2C bus.
 *
 * The DHT20 answers at 0x38 and uses a simple command/response protocol:
 *   write {0xAC, 0x33, 0x00}, wait >= 80 ms, read 7 bytes
 *   status, humidity[19:12], humidity[11:4], hum[3:0]/temp[19:16],
 *   temp[15:8], temp[7:0], crc
 *
 * Raw values are 20-bit fractions of the full scale: humidity 0..100 %RH,
 * temperature -50..+50 degC. The sensor is optional: if it does not answer the
 * read simply fails and the caller logs nothing.
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

typedef struct {
    float temperature_c;
    float humidity_pct;
} dht20_reading_t;

/* One measurement. Returns ESP_OK and fills `out` on success. */
esp_err_t dht20_read(dht20_reading_t *out);
