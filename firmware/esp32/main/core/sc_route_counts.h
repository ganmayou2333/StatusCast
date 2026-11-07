/*
 * HTTP route accounting -- the single source of truth for how many handlers the
 * server registers.
 *
 * Why this file exists: httpd_start allocates exactly max_uri_handlers slots, and
 * httpd_register_uri_handler returns ESP_ERR_HTTPD_HANDLERS_FULL once they are
 * gone. The routes are registered from two tables (net/http_server.c and
 * control/control_api.c), so counting only one of them under-allocates and the
 * handlers registered last are dropped at runtime with no compile error. That is
 * exactly how GET/DELETE /control/coredump, GET /control/audit and all seven
 * catch-all method handlers came to be unregistered while the build stayed green.
 *
 * Each table asserts its own size against the constant here, and
 * SC_HTTP_TOTAL_ROUTES is the only thing passed to httpd_start. Adding a route
 * therefore means updating this header, and the firmware fails to compile until
 * the numbers agree again.
 *
 * Deliberately dependency-free: host_test/ compiles against this header too, so
 * the constant is also checked without a board.
 */
#pragma once

/* Exact-path routes in net/http_server.c: /status/, /health, /diag. */
#define SC_HTTP_SPECIFIC_ROUTES 3

/* Authenticated routes in control/control_api.c under /control/. */
#define SC_CONTROL_ROUTE_COUNT 10

/* Catch-all entries in net/http_server.c, one per HTTP method. They are what
 * turns "wrong method on a known path" into 405 instead of 404. There are seven
 * because seven methods are registered: GET, POST, PUT, DELETE, PATCH, OPTIONS
 * and HEAD. */
#define SC_HTTP_CATCHALL_ROUTES 7

/* What httpd_start must be told to allocate. Never count a single table. */
#define SC_HTTP_TOTAL_ROUTES \
    (SC_HTTP_SPECIFIC_ROUTES + SC_CONTROL_ROUTE_COUNT + SC_HTTP_CATCHALL_ROUTES)
