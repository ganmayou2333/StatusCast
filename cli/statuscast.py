#!/usr/bin/env python3
"""StatusCast command-line client.

Prints the current iPhone battery state as a friendly line, or as the same
camelCase JSON snapshot served by the WinUI app at localhost:5099.

Usage:
    python cli/statuscast.py
    python cli/statuscast.py --json
    python cli/statuscast.py --watch 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from status_core import BatterySource, MediaSource, Status  # noqa: E402

VERSION = "2.0.0"

MODEL_NAMES = {
    "iPhone10,3": "X",
    "iPhone10,6": "X",
    "iPhone11,2": "XS",
    "iPhone11,4": "XS Max",
    "iPhone11,6": "XS Max",
    "iPhone11,8": "XR",
    "iPhone12,1": "11",
    "iPhone12,3": "11 Pro",
    "iPhone12,5": "11 Pro Max",
    "iPhone13,1": "12 mini",
    "iPhone13,2": "12",
    "iPhone13,3": "12 Pro",
    "iPhone13,4": "12 Pro Max",
    "iPhone14,2": "13 Pro",
    "iPhone14,3": "13 Pro Max",
    "iPhone14,4": "13 mini",
    "iPhone14,5": "13",
    "iPhone14,7": "14",
    "iPhone14,8": "14 Plus",
    "iPhone15,2": "14 Pro",
    "iPhone15,3": "14 Pro Max",
    "iPhone15,4": "15",
    "iPhone15,5": "15 Plus",
    "iPhone16,1": "15 Pro",
    "iPhone16,2": "15 Pro Max",
}


def model_name(product_type: str | None) -> str | None:
    if not product_type:
        return None
    return MODEL_NAMES.get(product_type, product_type)


async def collect() -> Status:
    status = Status()
    status = await BatterySource().fetch(status)
    status = MediaSource().fetch(status)
    return status


def render_text(status: Status) -> str:
    model = model_name(status.device_model) or "未知机型"
    parts = [f"📱 {model}"]

    if status.battery_level is None:
        parts.append("🔋 未知")
    else:
        battery = f"🔋 {status.battery_level}%"
        if status.is_charging:
            battery += " ⚡"
        parts.append(battery)

    if status.media_title:
        media = f"🎵 {status.media_title}"
        if status.media_artist:
            media += f" - {status.media_artist}"
    else:
        media = "🎵 暂无播放"
    parts.append(media)

    return " | ".join(parts)


def render_json(status: Status) -> str:
    now = int(time.time())
    payload = {
        "schema": 1,
        "battery": {
            "level": status.battery_level,
            "isCharging": status.is_charging,
            "updatedAt": now,
            "error": None,
        },
        "media": {
            "title": status.media_title,
            "artist": status.media_artist,
            "album": None,
            "playbackState": status.media_state,
            "durationSeconds": None,
            "elapsedSeconds": None,
            "playerName": None,
            "updatedAt": now,
            "error": None,
        },
        "generatedAt": now,
        "errors": [],
        "text": render_text(status),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Print iPhone battery state from a USB connection.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the same JSON snapshot shape as the local HTTP API",
    )
    parser.add_argument(
        "-w",
        "--watch",
        type=float,
        metavar="SECONDS",
        help="keep printing every N seconds; Ctrl+C stops",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"StatusCast CLI {VERSION}",
    )
    args = parser.parse_args()
    if args.watch is not None and args.watch <= 0:
        parser.error("--watch must be greater than 0")
    return args


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    args = parse_args()

    try:
        while True:
            status = asyncio.run(collect())
            if args.json:
                print(render_json(status))
            else:
                print(render_text(status))
            sys.stdout.flush()

            if args.watch is None:
                connected = status.device_model is not None or status.battery_level is not None
                return 0 if connected else 1

            time.sleep(args.watch)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
