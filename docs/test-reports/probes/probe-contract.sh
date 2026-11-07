#!/usr/bin/env bash
# Black-box contract probe for the StatusCast daemon.
# Oracle = docs/guide/http-api.md as committed in v3.4.0.
set -u
cd "$(dirname "$0")/../../.." || exit 1

PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf 'PASS  %-46s | %s\n' "$1" "$2"; }
bad()  { FAIL=$((FAIL+1)); printf 'FAIL  %-46s | %s\n' "$1" "$2"; }
chk()  { if [ "$2" = "$3" ]; then ok "$1" "$2"; else bad "$1" "got[$2] want[$3]"; fi; }

probe() { # name method path [extra curl args...]
  local name="$1" m="$2" p="$3"
  shift 3
  local hdr body code
  hdr=$(curl -s -X "$m" -D - -o /tmp/sc_body.$$ "$@" "http://localhost:5099$p" 2>/dev/null)
  code=$(printf '%s' "$hdr" | head -1 | tr -d '\r' | awk '{print $2}')
  body=$(cat /tmp/sc_body.$$ 2>/dev/null)
  rm -f /tmp/sc_body.$$
  LAST_CODE="$code"; LAST_HDR="$hdr"; LAST_BODY="$body"
}

echo "=============== A. ROUTING / METHOD MATRIX ==============="
probe "GET /status/"                 GET     "/status/"     ; chk "GET /status/"                 "$LAST_CODE" "200"
probe "GET /status (no trailing /)"  GET     "/status"      ; chk "GET /status (no trailing /)"  "$LAST_CODE" "404"
probe "GET /health"                  GET     "/health"      ; chk "GET /health"                  "$LAST_CODE" "200"
probe "GET /health/"                 GET     "/health/"     ; chk "GET /health/"                 "$LAST_CODE" "404"
probe "GET /Health (case)"           GET     "/Health"      ; chk "GET /Health (case)"           "$LAST_CODE" "404"
probe "GET /STATUS/ (case)"          GET     "/STATUS/"     ; chk "GET /STATUS/ (case)"          "$LAST_CODE" "404"
probe "GET /status/?x=1 (query)"     GET     "/status/?x=1" ; chk "GET /status/?x=1 (query)"     "$LAST_CODE" "200"
probe "GET /status/?x=1#frag"        GET     "/status/?a=b#c" ; chk "GET /status/?a=b#c"         "$LAST_CODE" "200"
probe "GET / (root)"                 GET     "/"            ; chk "GET / (root)"                 "$LAST_CODE" "404"
# KNOWN (F6, issue #13): POST/PUT sent with neither a body nor a Content-Length
# are rejected by HTTP.sys with 411 + HTML before the app sees them. Recorded but
# not scored, so this script stays a stable regression signal.
# See docs/test-reports/v3.4.0-bug-hunt.md.
known() { # label method path
  probe "$1" "$2" "$3"
  printf 'KNOWN %-46s | %s  (F6: HTTP.sys, not the app)\n' "$1" "$LAST_CODE"
}
known "POST /status/ (no body, no CL)" POST "/status/"
known "POST /nope    (no body, no CL)" POST "/nope"
known "PUT  /health  (no body, no CL)" PUT  "/health"
probe "DELETE /status/"              DELETE  "/status/"     ; chk "DELETE /status/"              "$LAST_CODE" "405"
probe "PATCH /health"                PATCH   "/health"      ; chk "PATCH /health"                "$LAST_CODE" "405"
probe "OPTIONS /status/"             OPTIONS "/status/"     ; chk "OPTIONS /status/"             "$LAST_CODE" "405"
probe "HEAD /status/"                HEAD    "/status/"     ; chk "HEAD /status/"                "$LAST_CODE" "405"
probe "POST /status/ (CL: 0)"        POST    "/status/"     -H 'Content-Length: 0'
chk "POST /status/ with Content-Length: 0" "$LAST_CODE" "405"
probe "GET //status/ (double slash)" GET     "//status/"    ; echo "INFO  GET //status/ => $LAST_CODE"
probe "GET /status// (trailing 2x)"  GET     "/status//"    ; echo "INFO  GET /status// => $LAST_CODE"
probe "GET /%73tatus/ (encoded s)"   GET     "/%73tatus/"   ; echo "INFO  GET /%73tatus/ => $LAST_CODE"
probe "GET /status/../health"        GET     "/status/../health" ; echo "INFO  GET /status/../health => $LAST_CODE"
LONG=$(printf 'a%.0s' $(seq 1 2000))
probe "GET /<2000 chars>"            GET     "/$LONG"       ; echo "INFO  GET 2000-char path => $LAST_CODE"

echo
echo "=============== B. HEADERS ON 200 /status/ ==============="
probe "GET /status/ for headers"     GET     "/status/"
h=$(printf '%s' "$LAST_HDR" | tr -d '\r')
for hdr in "Access-Control-Allow-Origin: *" "Cache-Control: no-store" "Content-Type: application/json; charset=utf-8"; do
  if printf '%s\n' "$h" | grep -qiF "$hdr"; then ok "header $hdr" "present"; else bad "header $hdr" "MISSING (headers: $(printf '%s' "$h" | tr '\n' ' '))"; fi
done
clen=$(printf '%s\n' "$h" | grep -i '^Content-Length:' | awk '{print $2}' | tr -d '\r')
blen=$(printf '%s' "$LAST_BODY" | wc -c | tr -d ' ')
chk "Content-Length == body bytes" "$clen" "$blen"

echo
echo "=============== C. BODY EXACTNESS ==============="
probe "GET /nope body"               GET     "/nope"
chk "404 body" "$LAST_BODY" '{"error":"not found"}'
probe "POST /status/ body"           POST    "/status/"
chk "405 body" "$LAST_BODY" '{"error":"method not allowed"}'
probe "GET /health body"             GET     "/health"
echo "INFO  /health body = $LAST_BODY"
if printf '%s' "$LAST_BODY" | grep -qE '^\{"ok":true,"deviceCount":[0-9]+,"generatedAt":[0-9]+,"schema":3\}$'; then
  ok "/health 200 shape" "$LAST_BODY"
else bad "/health 200 shape" "$LAST_BODY"; fi
# `od`/`cksum` are not guaranteed in the MSYS bash used on this machine, so read
# the last byte through the repo's own Python.
last_byte=$(curl -s "http://localhost:5099/nope" | ./.venv/Scripts/python.exe -c "import sys; print(repr(sys.stdin.buffer.read()[-1:]))")
chk "404 body has no trailing newline" "$last_byte" "b'}'"

echo
echo "=============== D. STATE-DEPENDENT (== 503 paths) ==============="
mv run/status.json /tmp/sc_status_backup.json

probe "no snapshot: /health"         GET     "/health"
chk "no snapshot: /health"           "$LAST_CODE" "503"
chk "no snapshot: /health body"      "$LAST_BODY" '{"ok":false,"deviceCount":null,"generatedAt":null,"schema":null}'
probe "no snapshot: /status/"        GET     "/status/"
chk "no snapshot: /status/"          "$LAST_CODE" "503"
chk "no snapshot: /status/ body"     "$LAST_BODY" '{"error":"status snapshot not ready"}'

printf '{oops not json' > run/status.json
probe "invalid JSON: /health"        GET     "/health"
chk "invalid JSON: /health"          "$LAST_CODE" "503"
probe "invalid JSON: /status/"       GET     "/status/"
chk "invalid JSON: /status/ (raw 200)" "$LAST_CODE" "200"

printf '{"schema":3,"generatedAt":1,"devices":{}}' > run/status.json
probe "devices is object: /health"   GET     "/health"
chk "devices is object: /health"     "$LAST_CODE" "503"
probe "devices is object: /status/"  GET     "/status/"
chk "devices is object: /status/"    "$LAST_CODE" "200"

printf '{"devices":[]}' > run/status.json
probe "no schema/generatedAt: /health" GET   "/health"
chk "no schema/generatedAt: /health" "$LAST_CODE" "200"
echo "INFO  body = $LAST_BODY"

printf '' > run/status.json
probe "empty file: /health"          GET     "/health"
chk "empty file: /health"            "$LAST_CODE" "503"

printf '{"devices":[]}\n' > run/status.json
probe "devices:[] with newline: /health" GET "/health"
chk "devices:[] with newline: /health" "$LAST_CODE" "200"

mv /tmp/sc_status_backup.json run/status.json

echo
echo "=============== E. CONCURRENCY ==============="
conc_fail=0
for i in $(seq 1 20); do
  ( c=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:5099/status/"); [ "$c" = "200" ] || echo "  concurrent #$i => $c" ) &
done
wait
probe "after 20 concurrent"          GET     "/status/"
chk "after 20 concurrent: /status/"  "$LAST_CODE" "200"

echo
echo "=============== SUMMARY ==============="
echo "PASS=$PASS FAIL=$FAIL"
