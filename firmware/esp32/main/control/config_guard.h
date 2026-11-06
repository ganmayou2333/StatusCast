/*
 * config_guard -- protect the board against a bad config push.
 *
 * POST /control/config can brick the data path (wrong PC URL, wrong interval).
 * The guard keeps the previous file, arms a rollback marker, and rolls back when
 * either
 *   - the current boot never managed to publish a snapshot within 90 s
 *     (a config that crashes or wedges the board), or
 *   - the board reboots while the marker is still armed (crash loop): init()
 *     restores the previous config before the first round.
 *
 * The marker lives in NVS so it survives a reset; WiFi credentials live in NVS
 * too and are not part of config.json, so a rollback can never lock the board out
 * of the network.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

void config_guard_init(void);
/* Back up the current file, write `json`, arm the rollback and reload. */
esp_err_t config_guard_apply(const char *json, size_t len);
/* Called after a snapshot was published successfully. */
void config_guard_note_publish(void);
/* Called once per round; restores the previous config when the deadline passed. */
void config_guard_poll(void);
bool config_guard_pending(void);
