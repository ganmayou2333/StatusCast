/*
 * audit -- a small ring of the last control operations, readable through
 * `GET /control/audit`. Only /control/... actions are recorded (never the token).
 */
#pragma once

#include <stddef.h>

void audit_init(void);
void audit_record(const char *action, const char *result, const char *peer_ip);
/* Render the ring as a JSON array; returns bytes written. */
size_t audit_render_json(char *dst, size_t cap);
