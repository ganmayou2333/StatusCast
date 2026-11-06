/*
 * pc_bridge -- pull the Windows daemon's /status/ and merge its rows.
 *
 * The daemon remains the only source for ios/android/windows rows. When it is
 * unreachable we reproduce the daemon's own stale path: reuse the last good
 * rows, wipe battery/media, set error "读取超时", drop transport. A PC that was
 * never reachable contributes no rows at all (no fake phone entries).
 */
#pragma once

#include <stdint.h>

#include "sc_core.h"

/* Forget cached rows (e.g. after PcFeed.url changes). */
void pc_bridge_reset(void);

/* Fetch + merge. Returns the number of rows appended (last-good or stale). */
int pc_bridge_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now);
