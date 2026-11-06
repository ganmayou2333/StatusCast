#pragma once

#include <stdint.h>

/* 8x16 ASCII font, characters 0x20..0x7E, column-major bytes.
 * Bit 0 of a column byte is the top scan line of that page. */
extern const uint8_t sc_font8x16_bits[95][8][2];
