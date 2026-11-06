#pragma once

#include <stdint.h>

/* 6x8 ASCII font, characters 0x20..0x7E, column-major bytes.
 * Bit 0 of a column byte is the top scan line of that page. */
extern const uint8_t sc_font6x8_bits[95][6][1];
