/*
 * sc_limits -- the snapshot size budget, in one place.
 *
 * Why this file exists: the render buffer (app_main.c), the /status/ response
 * copy buffer (net/http_server.c) and the number of devices the model allows
 * (SC_MAX_DEVICES) used to be three unrelated numbers. Two of them were 8192 by
 * coincidence, and the third promised 16 devices that did not fit. When a round
 * did not fit, sc_snapshot_render() returned 0, app_main logged one line and
 * published nothing -- so the board served the previous round's snapshot forever
 * while /health reported it as current.
 *
 * SC_SNAPSHOT_JSON_BUDGET is the minimum the firmware build must provide. The
 * firmware asserts CONFIG_SC_MAX_SNAPSHOT_JSON >= this (see net/http_server.c
 * and app_main.c), and the host test asserts that SC_MAX_DEVICES fully populated
 * rows actually fit inside it. Lowering the Kconfig value below the budget now
 * fails the build instead of silently corrupting the served state.
 *
 * Measured: 16 rows with every string field at SC_STR_LEN and both nekotrak
 * passthrough subtrees at SC_RAW_JSON_LEN need 65,985 bytes. That is the model's
 * own upper bound, not a guess -- the host test renders it and fails if it stops
 * fitting. The board has 8 MB of PSRAM, so paying 72 KiB here is cheap; a round
 * that does not fit is not.
 *
 * Deliberately dependency-free: host_test/ includes this too.
 */
#pragma once

#define SC_SNAPSHOT_JSON_BUDGET 73728
