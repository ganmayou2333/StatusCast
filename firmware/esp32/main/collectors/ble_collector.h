/*
 * ble_collector -- NimBLE central for `BleDevices[]`.
 *
 * Mirrors collectors/ble_collector.py: every round performs ONE shared scan
 * (up to 4 s) for every configured entry, then connects to each peripheral that
 * was seen, reads the Battery Service (0x180F/0x2A19) and/or samples the Heart
 * Rate Measurement characteristic (0x180D/0x2A37) from its first notification,
 * and drops the link again.
 *
 * A peripheral that is not advertising is a normal device row: `error` stays
 * absent and `ble.statusText` says 未发现.  A peripheral that advertises but
 * cannot be connected still proves liveness, so its row is online with
 * `transport.degraded = true` and `ble.statusText` 已发现未连接.
 *
 * All NimBLE work happens on the host task; the two public entry points only
 * start the procedures and wait for their completion events.  They serialise on
 * one mutex, so a console scan never interleaves with a collector round.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "sc_core.h"

/* Initialise NimBLE once (idempotent). Safe to call from the main task. */
void ble_collector_init(void);

/* Run one BLE round: one shared scan of up to 4 seconds for all configured
 * devices, then per matched device connect/read/subscribe.
 * Appends one sc_device_t per `cfg->ble` entry to `snapshot` (platform "ble"),
 * in config order. `now` is unix seconds. Returns the number of rows appended. */
/* Sample every configured peripheral. `deadline_ms` is the round's wall-clock
 * budget (sc_mono_ms base); the BLE stage additionally caps itself at
 * BLE_ROUND_BUDGET_MS, whichever comes first. */
int ble_collector_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now,
                      int64_t deadline_ms);

/* Runs one 4 s scan and lists up to `max` advertising devices.
 * Fills names[i] (ASCII, may be empty), addrs[i] ("AA:BB:CC:DD:EE:FF"), rssi[i].
 * Returns the number of entries written (0 on failure / not synced). */
int ble_collector_scan_list(int max, char names[][32], char addrs[][18], int8_t *rssi);
