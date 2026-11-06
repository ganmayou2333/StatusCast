/*
 * control_api -- the Windows -> board control surface.
 *
 * Every route under /control/... requires the X-StatusCast-Token header (token in
 * NVS, default from Kconfig, rotatable through POST /control/token). The public
 * read-only surface (/status/, /health, /diag) stays unauthenticated so NekoTool
 * keeps working unchanged.
 *
 * Routes are registered before the catch-all handlers in http_server.c so the
 * wildcard routes cannot shadow them.
 */
#pragma once

#include "esp_err.h"
#include "esp_http_server.h"

/* Registers the /control/... handlers. Must be called before the wildcard routes. */
esp_err_t control_api_register(httpd_handle_t server);
