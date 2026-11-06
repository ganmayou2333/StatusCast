#include "audit.h"

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

#include "app_state.h"
#include "mem_util.h"

#define AUDIT_SLOTS 16
#define AUDIT_ACTION_MAX 20
#define AUDIT_RESULT_MAX 24
#define AUDIT_IP_MAX 16

typedef struct {
    int64_t at;
    char action[AUDIT_ACTION_MAX];
    char result[AUDIT_RESULT_MAX];
    char peer[AUDIT_IP_MAX];
} audit_entry_t;

static audit_entry_t *s_entries;
static int s_next;
static int s_count;
static SemaphoreHandle_t s_lock;

void audit_init(void)
{
    if (s_entries != NULL) {
        return;
    }
    s_entries = (audit_entry_t *)sc_psram_alloc(sizeof(audit_entry_t) * AUDIT_SLOTS);
    if (s_entries == NULL) {
        return;
    }
    memset(s_entries, 0, sizeof(audit_entry_t) * AUDIT_SLOTS);
    s_lock = xSemaphoreCreateMutex();
    if (s_lock == NULL) {
        s_entries = NULL;
    }
}

void audit_record(const char *action, const char *result, const char *peer_ip)
{
    if (s_entries == NULL || s_lock == NULL) {
        return;
    }
    (void)xSemaphoreTake(s_lock, portMAX_DELAY);
    audit_entry_t *slot = &s_entries[s_next];
    slot->at = app_now_unix();
    (void)snprintf(slot->action, sizeof(slot->action), "%s", action != NULL ? action : "-");
    (void)snprintf(slot->result, sizeof(slot->result), "%s", result != NULL ? result : "-");
    (void)snprintf(slot->peer, sizeof(slot->peer), "%s", peer_ip != NULL ? peer_ip : "-");
    s_next = (s_next + 1) % AUDIT_SLOTS;
    if (s_count < AUDIT_SLOTS) {
        s_count += 1;
    }
    (void)xSemaphoreGive(s_lock);
}

size_t audit_render_json(char *dst, size_t cap)
{
    if (dst == NULL || cap == 0 || s_entries == NULL || s_lock == NULL) {
        return 0;
    }
    size_t used = 0;
    (void)snprintf(dst, cap, "[");
    used = 1;
    (void)xSemaphoreTake(s_lock, portMAX_DELAY);
    int start = (s_next - s_count + AUDIT_SLOTS) % AUDIT_SLOTS;
    for (int i = 0; i < s_count; ++i) {
        const audit_entry_t *entry = &s_entries[(start + i) % AUDIT_SLOTS];
        char chunk[160];
        int written = snprintf(chunk, sizeof(chunk),
                               "%s{\"at\":%lld,\"action\":\"%s\",\"result\":\"%s\","
                               "\"peer\":\"%s\"}",
                               i == 0 ? "" : ",", (long long)entry->at, entry->action,
                               entry->result, entry->peer);
        if (written <= 0 || used + (size_t)written + 2 > cap) {
            break;
        }
        memcpy(dst + used, chunk, (size_t)written);
        used += (size_t)written;
    }
    (void)xSemaphoreGive(s_lock);
    dst[used++] = ']';
    dst[used] = '\0';
    return used;
}
