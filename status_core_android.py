#!/usr/bin/env python3
"""Legacy single-device Android CLI.

The daemon now consumes the merged JSON from ``status_core.py``. This wrapper
is kept for manual diagnostics and for users of the old STATUS-line interface.
"""

from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collectors.android_collector import AndroidCollector

NA_STATUS = "STATUS:BATTERY:NA|CHARGING:NA|MEDIA:NA|NA|unknown|MODEL:NA"


def _format_legacy(data: dict) -> str:
    battery = "NA" if data.get("batteryLevel") is None else str(data["batteryLevel"])
    charging_value = data.get("isCharging")
    charging = "NA" if charging_value is None else str(bool(charging_value)).lower()
    title = data.get("mediaTitle") or "NA"
    artist = data.get("mediaArtist") or "NA"
    state = data.get("mediaState") or "unknown"
    model = data.get("model") or "NA"
    app = data.get("foregroundApp") or "NA"
    app_name = data.get("foregroundAppName") or "NA"
    return (
        f"STATUS:BATTERY:{battery}|CHARGING:{charging}"
        f"|MEDIA:{title}|{artist}|{state}|MODEL:{model}"
        f"|APP:{app}|APPNAME:{app_name}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Android status via ADB.")
    parser.add_argument("--adb", default=None, help="adb executable")
    parser.add_argument("--serial", default=None, help="Android serial")
    args = parser.parse_args()

    try:
        data = AndroidCollector(adb=args.adb, serial=args.serial).get_data()
    except Exception as exc:
        print(NA_STATUS)
        print(str(exc), file=sys.stderr)
        return 1

    print(_format_legacy(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
