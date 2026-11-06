#include "log_ring.h"

#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "mem_util.h"

#define LOG_RING_LINES 128
#define LOG_LINE_MAX 128

static char (*s_lines)[LOG_LINE_MAX];
static int s_next;
static int s_count;
static SemaphoreHandle_t s_lock;
static vprintf_like_t s_previous;
static bool s_in_hook;

static int log_hook(const char *format, va_list args)
{
    if (!s_in_hook && s_lines != NULL) {
        s_in_hook = true;

        char line[LOG_LINE_MAX];
        va_list copy;
        va_copy(copy, args);
        int produced = vsnprintf(line, sizeof(line), format, copy);
        va_end(copy);

        if (produced > 0) {
            size_t length = strlen(line);
            while (length > 0 && (line[length - 1] == '\n' || line[length - 1] == '\r')) {
                line[--length] = '\0';
            }
            if (length > 0 && s_lock != NULL && xSemaphoreTake(s_lock, 0) == pdTRUE) {
                memcpy(s_lines[s_next], line, length + 1);
                s_next = (s_next + 1) % LOG_RING_LINES;
                if (s_count < LOG_RING_LINES) {
                    s_count += 1;
                }
                (void)xSemaphoreGive(s_lock);
            }
        }
        s_in_hook = false;
    }
    if (s_previous != NULL) {
        return s_previous(format, args);
    }
    return vprintf(format, args);
}

void log_ring_init(void)
{
    if (s_lines != NULL) {
        return;
    }
    s_lines = (char(*)[LOG_LINE_MAX])sc_psram_alloc((size_t)LOG_RING_LINES * LOG_LINE_MAX);
    if (s_lines == NULL) {
        return;
    }
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        s_lines = NULL;
        return;
    }
    s_next = 0;
    s_count = 0;
    s_previous = esp_log_set_vprintf(log_hook);
}

size_t log_ring_dump(char *dst, size_t cap, int max_lines)
{
    if (dst == NULL || cap == 0 || s_lines == NULL || s_lock == NULL) {
        return 0;
    }
    if (max_lines <= 0 || max_lines > LOG_RING_LINES) {
        max_lines = LOG_RING_LINES;
    }

    size_t used = 0;
    dst[0] = '\0';
    (void)xSemaphoreTake(s_lock, portMAX_DELAY);
    int available = s_count;
    int start = (s_next - available + LOG_RING_LINES) % LOG_RING_LINES;
    int from = available > max_lines ? available - max_lines : 0;
    for (int i = from; i < available; ++i) {
        const char *line = s_lines[(start + i) % LOG_RING_LINES];
        size_t length = strlen(line);
        if (used + length + 2 > cap) {
            break;
        }
        memcpy(dst + used, line, length);
        used += length;
        dst[used++] = '\n';
        dst[used] = '\0';
    }
    (void)xSemaphoreGive(s_lock);
    return used;
}
