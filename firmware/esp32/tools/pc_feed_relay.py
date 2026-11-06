#!/usr/bin/env python3
"""Loopback-only daemon -> LAN relay for the ESP32 PC feed.

The Windows daemon registers HttpListener prefixes for `localhost`/`127.0.0.1`
only, so an ESP32 on the LAN cannot pull its `/status/`. Until the daemon grows an
opt-in LAN bind (see .scratch/esp32-port/spec.md, open item), this relay exposes
the two read-only endpoints on a LAN port:

    python tools/pc_feed_relay.py --listen-port 5100

It forwards only GET /status/ and GET /health, copies the upstream status code,
body bytes and Content-Type, and refuses everything else with the same
404/405 bodies the daemon uses. No writes, no caching, no auth (same trust level
as the daemon's own loopback API).
"""
from __future__ import annotations

import argparse
import http.server
import urllib.error
import urllib.request

UPSTREAM = "http://localhost:5099"

HTTP_NOT_FOUND = b'{"error":"not found"}'
HTTP_METHOD_NOT_ALLOWED = b'{"error":"method not allowed"}'
HTTP_BAD_GATEWAY = b'{"error":"pc daemon unreachable"}'


class RelayHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "StatusCastPcFeed/0.1"
    upstream = UPSTREAM

    def _send(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server API)
        path = self.path.split("?", 1)[0]
        if path not in ("/status/", "/health"):
            self._send(404, HTTP_NOT_FOUND)
            return
        request = urllib.request.Request(self.upstream + path, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                body = response.read()
                self._send(response.status, body, response.headers.get("Content-Type", "application/json; charset=utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read() or HTTP_BAD_GATEWAY
            self._send(error.code, body, error.headers.get("Content-Type", "application/json; charset=utf-8"))
        except Exception:  # daemon down / timeout
            self._send(503, HTTP_BAD_GATEWAY)

    def _reject(self):
        self._send(405, HTTP_METHOD_NOT_ALLOWED)

    do_POST = _reject  # noqa: N815
    do_PUT = _reject  # noqa: N815
    do_DELETE = _reject  # noqa: N815
    do_PATCH = _reject  # noqa: N815
    do_OPTIONS = _reject  # noqa: N815
    do_HEAD = _reject  # noqa: N815

    def log_message(self, format, *args):  # quieter output
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay the loopback StatusCast daemon onto the LAN for the ESP32.")
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=5100)
    parser.add_argument("--upstream", default=UPSTREAM)
    args = parser.parse_args()

    RelayHandler.upstream = args.upstream.rstrip("/")

    server = http.server.ThreadingHTTPServer((args.listen_host, args.listen_port), RelayHandler)
    print(f"pc feed relay: http://{args.listen_host}:{args.listen_port}/status/ -> {RelayHandler.upstream}/status/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
