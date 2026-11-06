/*
 * http_server -- the compatibility surface.
 *
 * Reproduces the daemon's status codes, bodies and headers exactly:
 *   GET  /status/  -> 200 (published body verbatim) | 503
 *   GET  /health   -> 200 {"ok":true,...}           | 503 {"ok":false,...}
 *   any non-GET    -> 405 {"error":"method not allowed"}
 *   any other path -> 404 {"error":"not found"}
 * Every response carries CORS *, no-store and application/json; charset=utf-8.
 *
 * Difference from the daemon, by design: we bind 0.0.0.0 so NekoTool on the LAN
 * can reach us, and there is no HTTP.sys-style pre-handler 400/411.
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

esp_err_t http_server_start(uint16_t port);
void http_server_stop(void);
