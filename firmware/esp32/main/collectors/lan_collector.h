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
int lan_collector_run(const sc_config_t *cfg, sc_snapshot_t *snapshot, int64_t now);
