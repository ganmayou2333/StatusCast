/*
 * sc_json -- minimal JSON output writer with .NET-compatible escaping.
 *
 * Why a custom writer instead of cJSON_Print:
 * the daemon (C#) reads run/status.json with File.ReadAllTextAsync and serves the
 * bytes verbatim, and .NET's default JavaScriptEncoder escapes every non-ASCII
 * code point as \uXXXX (plus '+' '<' '>' '&' '\''). PowerShell 5.1's
 * Invoke-RestMethod is unreliable about charset handling, so NekoTool only stays
 * byte-stable if we keep that escaping. cJSON_Print emits raw UTF-8, so we build
 * the snapshot ourselves.
 *
 * The writer never allocates: it appends into a caller-provided buffer and sets
 * `oom` when the buffer is too small, leaving a NUL-terminated prefix.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct {
    char *buf;
    size_t cap;
    size_t len;
    bool oom;
} sc_json_w_t;

void sc_json_w_init(sc_json_w_t *w, char *buf, size_t cap);

/* Append raw text (caller guarantees it is valid JSON syntax). */
void sc_json_raw(sc_json_w_t *w, const char *text);

/* Append a single byte. */
void sc_json_byte(sc_json_w_t *w, char c);

/* Append a JSON string literal, double quotes included. NULL -> null. */
void sc_json_str(sc_json_w_t *w, const char *s);

/* Append `"key":` (relative order is the caller's responsibility). */
void sc_json_key(sc_json_w_t *w, const char *key);

void sc_json_int(sc_json_w_t *w, int64_t value);
void sc_json_bool(sc_json_w_t *w, bool value);
void sc_json_null(sc_json_w_t *w);

/* Escape into a standalone buffer (used by tests and by the \uXXXX checks).
 * Returns the number of bytes written, excluding the terminating NUL. */
size_t sc_json_escape(const char *s, char *out, size_t out_cap);

/* Append the escaped form of an optional string: null when !has. */
void sc_json_opt_str(sc_json_w_t *w, const char *s, bool has);
