/*
 * lan_collector -- LAN presence probing for `Consoles[]`.
 *
 * Same hysteresis as the Python console_collector (2 consecutive failures mark a
 * host offline, one success brings it straight back), same row shape. The one
 * structural difference is documented in the spec: lwIP has no SendARP
 * equivalent, so the probe chain is ICMP ping -> TCP port connect and
 * `console.method` will never be "arp".
 */
#pragma once

#include <stdint.h>

#include "sc_core.h"

/* Append one "console" row per configured host. Returns the number appended. */
/* Probe every configured console. `deadline_ms` is the round's wall-clock
 * budget (sc_mono_ms base): once it passes, the remaining hosts and ports are
 * skipped so a slow subnet cannot stretch the round past its period. */
int lan_collector_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now,
                      int64_t deadline_ms);
