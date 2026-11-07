#!/usr/bin/env bash
# Focused probe: how non-GET requests are actually answered, and what
# Content-Type every response carries.
set -u
cd "$(dirname "$0")/../../.." || exit 1
BASE=http://localhost:5099

show() { # label method path [extra curl args...]
  local label="$1" m="$2" p="$3"; shift 3
  local hdr body code ctype
  hdr=$(curl -s -X "$m" -D - -o /tmp/p2.$$ "$BASE$p" "$@" 2>/dev/null)
  code=$(printf '%s' "$hdr" | head -1 | tr -d '\r' | awk '{print $2}')
  ctype=$(printf '%s' "$hdr" | tr -d '\r' | grep -i '^Content-Type:' | head -1 | cut -d' ' -f2-)
  body=$(head -c 90 /tmp/p2.$$ 2>/dev/null | tr '\n' ' ')
  rm -f /tmp/p2.$$
  printf '%-52s code=%-4s ctype=%-32s body=%s\n' "$label" "${code:-<none>}" "${ctype:-<none>}" "$body"
}

echo "=== non-GET, NO body and NO Content-Length ==="
show "POST  /status/  (no body)"        POST   /status/
show "POST  /nope     (no body)"        POST   /nope
show "PUT   /health   (no body)"        PUT    /health
show "PUT   /nope     (no body)"        PUT    /nope
show "PATCH /health   (no body)"        PATCH  /health
show "DELETE /health  (no body)"        DELETE /health
show "OPTIONS /status/(no body)"        OPTIONS /status/
show "HEAD  /status/  (no body)"        HEAD   /status/

echo
echo "=== non-GET WITH a body / explicit length ==="
show "POST  /status/  (Content-Length: 0)" POST   /status/ -H 'Content-Length: 0'
show "POST  /nope     (Content-Length: 0)" POST   /nope    -H 'Content-Length: 0'
show "PUT   /health   (Content-Length: 0)" PUT    /health  -H 'Content-Length: 0'
show "POST  /status/  (json body)"         POST   /status/ -H 'Content-Type: application/json' -d '{"a":1}'
show "PUT   /health   (json body)"         PUT    /health  -H 'Content-Type: application/json' -d '{"a":1}'
show "POST  /nope     (json body)"         POST   /nope    -H 'Content-Type: application/json' -d '{"a":1}'
show "POST  /status/  (chunked)"           POST   /status/ -H 'Transfer-Encoding: chunked' -d 'abc'

echo
echo "=== GET responses: exact body bytes / trailing newline ==="
for path in "/nope" "/health" "/status/"; do
  curl -s "http://localhost:5099$path" | ./.venv/Scripts/python.exe -c "
import sys
b = sys.stdin.buffer.read()
print(f'{sys.argv[1]:10s} bytes={len(b):5d} first={b[:1]!r} last={b[-1:]!r}')
" "$path"
done

echo
echo "=== 404/405 body + headers exactly ==="
curl -s -D - -o /tmp/b404 "http://localhost:5099/nope"   | tr -d '\r'
echo "404 body: [$(cat /tmp/b404)]"
echo "---"
curl -s -X DELETE -D - -o /tmp/b405 "http://localhost:5099/status/" | tr -d '\r'
echo "405 body: [$(cat /tmp/b405)]"

echo
echo "=== /health when top-level is an array ==="
cp run/status.json /tmp/s_keep.json
printf '[]' > run/status.json
printf 'top-level []      -> /health %s | /status/ %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/health)" \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/status/)"
printf '{"devices":"x"}' > run/status.json
printf 'devices string    -> /health %s | /status/ %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/health)" \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/status/)"
printf '{"devices":null}' > run/status.json
printf 'devices null      -> /health %s | /status/ %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/health)" \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/status/)"
printf '   ' > run/status.json
printf 'whitespace only   -> /health %s | /status/ %s\n' \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/health)" \
  "$(curl -s -o /dev/null -w '%{http_code}' $BASE/status/)"
cp /tmp/s_keep.json run/status.json
