/*
 * sc_wire_parse -- turn a PC daemon /status/ body into sc_device_t rows.
 *
 * The PC daemon is the only source of ios/android/windows rows on the ESP32.
 * We re-project them instead of copying bytes so that:
 *   - the stale path can rewrite a row (clear battery/media, set the error text,
 *     drop transport) without string surgery;
 *   - the additive blocks keep the contract's field order and null semantics.
 * Unknown members are dropped, which is exactly what the daemon's own
 * projection does (StatusCoordinator.BuildDevicePayload).
 *
 * Pure logic: no IDF dependency, covered by the host tests.
 */
#pragma once

#include <stddef.h>

#include "sc_core.h"

/* Parse a schema-3 snapshot body. Returns the number of rows written (0..max),
 * or -1 when the payload is not a JSON object. Malformed individual rows are
 * skipped rather than aborting the whole merge. */
int sc_wire_parse_devices(const char *json, size_t len, const sc_alias_table_t *aliases,
                          sc_device_t *out, int max_devices);

/* Append parsed rows into a snapshot (capacity-limited). Returns how many were
 * actually appended. */
int sc_snapshot_merge(sc_snapshot_t *snapshot, const sc_device_t *rows, int count);

/* Apply the daemon's stale-row projection: keep identity and additive blocks
 * that survive (console/node/ble/nekotrak), wipe per-round values, force
 * mediaState "unknown", set the error text, drop transport. */
void sc_device_mark_stale(sc_device_t *device, const char *error_text);

#define SC_STALE_ERROR_TEXT "读取超时"
