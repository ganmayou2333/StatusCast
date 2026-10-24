#!/usr/bin/env python3
"""StatusCast Python dispatcher.

Runs every registered collector, isolates failures per device, and prints one
standard JSON snapshot (the same shape the daemon writes to run/status.json):

    {
      "schema": 3,
      "generatedAt": 1792860000,
      "devices": [
        {"id": "iphone", "platform": "ios", ..., "error": null},
        {"id": "android", "platform": "android", ..., "error": null}
      ]
    }

A disconnected device only sets ``error`` on its own entry; it never prevents
the other collector from returning data.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collectors.android_collector import AndroidCollector
from collectors.iphone_collector import IPhoneCollector


def collect_all(
    adb: str | None = None,
    serial: str | None = None,
) -> dict[str, Any]:
    """Run all collectors and merge the results into one snapshot dict."""
    collectors = [
        IPhoneCollector(),
        AndroidCollector(adb=adb, serial=serial),
    ]

    devices: list[dict[str, Any]] = []
    for collector in collectors:
        try:
            devices.append(collector.get_data())
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            devices.append(collector.error_payload(message))

    return {
        "schema": 3,
        "generatedAt": int(time.time()),
        "devices": devices,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect iPhone + Android status and print one JSON snapshot."
    )
    parser.add_argument(
        "--adb",
        default=None,
        help="adb executable (default: ADB env var or 'adb')",
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="Android serial (default: ANDROID_SERIAL env var)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (default: compact one-line JSON).",
    )
    args = parser.parse_args()

    payload = collect_all(adb=args.adb, serial=args.serial)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
