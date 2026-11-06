/*
 * sc_snapshot -- render an sc_snapshot_t as the schema-3 wire JSON.
 *
 * The key order, the additive-block order and the \uXXXX escaping are the
 * contract: NekoTool's PowerShell scripts render straight off these fields, and
 * the C#/Python/Python-test sides all validate against schema/status-contract.json.
 * Deliberate deviation from the daemon: the body is compact (no 2-space indent,
 * no CRLF) because that only affects whitespace, not JSON semantics, and it
 * keeps the ESP32 response small.
 */
#pragma once

#include <stddef.h>

#include "sc_core.h"

/* Render into `buf` (NUL terminated). Returns the number of bytes written
 * (excluding the NUL), or 0 when the buffer is too small / oom. */
size_t sc_snapshot_render(const sc_snapshot_t *snapshot, char *buf, size_t cap);

/* Bytes needed for the current content (same as the return value, but the
 * function tolerates cap == 0). */
size_t sc_snapshot_render_len(const sc_snapshot_t *snapshot);
