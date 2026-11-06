/*
 * mem_util -- allocate the big, non-ISR buffers outside internal DRAM.
 *
 * The snapshot model (~60 KB) plus the JSON/config buffers would otherwise sit
 * in .dram0.bss and blow the DRAM segment: the ESP32-S3 has 8 MB of PSRAM on
 * this board, so large buffers are heap-allocated with a PSRAM preference and an
 * internal-RAM fallback.
 */
#pragma once

#include <stddef.h>

#include "esp_heap_caps.h"

static inline void *sc_psram_alloc(size_t size)
{
    void *block = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (block == NULL) {
        block = heap_caps_malloc(size, MALLOC_CAP_8BIT);
    }
    return block;
}
