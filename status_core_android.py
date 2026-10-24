#!/usr/bin/env python3
"""StatusCast Android collector.

Uses ADB (no root required) to read:
  - battery level and charging state
  - device model / manufacturer
  - foreground app package (with an optional Chinese display name)
  - best-effort media session title / artist / state

Output is compatible with status_core.py:
STATUS:BATTERY:87|CHARGING:true|MEDIA:Title|Artist|playing|MODEL:Pixel 7|APP:com.example|APPNAME:Example
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


APP_NAMES = {
    "com.tencent.mm": "微信",
    "com.tencent.mobileqq": "QQ",
    "com.ss.android.ugc.aweme": "抖音",
    "tv.danmaku.bili": "哔哩哔哩",
    "com.netease.cloudmusic": "网易云音乐",
    "com.tencent.qqmusic": "QQ 音乐",
    "com.google.android.youtube": "YouTube",
    "com.android.chrome": "Chrome",
    "com.microsoft.emmx": "Edge",
    "com.spotify.music": "Spotify",
    "com.xiaomi.mihome": "米家",
    "com.eg.android.AlipayGphone": "支付宝",
    "com.taobao.taobao": "淘宝",
    "com.jingdong.app.mall": "京东",
    "com.zhihu.android": "知乎",
    "com.sina.weibo": "微博",
    "com.tencent.wework": "企业微信",
    "com.discord": "Discord",
    "com.steam.android": "Steam",
}

PLAYBACK_STATES = {
    "0": "none",
    "1": "stopped",
    "2": "paused",
    "3": "playing",
    "4": "fast_forwarding",
    "5": "rewinding",
    "6": "buffering",
    "7": "error",
}


def sanitize(value: str | None) -> str:
    if not value:
        return "NA"
    return value.replace("|", "/").replace("\r", " ").replace("\n", " ").strip() or "NA"


def run_adb(adb: str, serial: str | None, args: list[str], timeout: int = 8) -> str:
    command = [adb]
    if serial:
        command += ["-s", serial]
    command += args
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"adb exit code {result.returncode}")
    return result.stdout


def pick_serial(adb: str, requested: str | None) -> str | None:
    if requested:
        return requested
    output = run_adb(adb, None, ["devices"])
    for line in output.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            return parts[0]
    return None


def get_model(adb: str, serial: str | None) -> str | None:
    model = run_adb(adb, serial, ["shell", "getprop", "ro.product.model"]).strip()
    manufacturer = run_adb(
        adb, serial, ["shell", "getprop", "ro.product.manufacturer"]
    ).strip()
    if not model:
        return None
    if manufacturer and manufacturer.lower() not in model.lower():
        return f"{manufacturer} {model}"
    return model


def get_battery(adb: str, serial: str | None) -> tuple[int | None, bool | None]:
    output = run_adb(adb, serial, ["shell", "dumpsys", "battery"])
    level_match = re.search(r"^\s*level:\s*(\d+)", output, re.MULTILINE)
    status_match = re.search(r"^\s*status:\s*(\d+)", output, re.MULTILINE)
    level = int(level_match.group(1)) if level_match else None
    status = int(status_match.group(1)) if status_match else None

    powered = False
    for key in ("AC powered", "USB powered", "Wireless powered", "Dock powered"):
        if re.search(rf"^\s*{re.escape(key)}:\s*true", output, re.MULTILINE):
            powered = True
            break

    charging = None
    if status is not None:
        charging = status in (2, 5) or powered
    elif powered:
        charging = True

    return level, charging


def get_foreground_app(adb: str, serial: str | None) -> str | None:
    try:
        output = run_adb(adb, serial, ["shell", "dumpsys", "activity", "activities"])
        match = re.search(
            r"(?:mResumedActivity|topResumedActivity).*?\s([A-Za-z0-9_.]+)/",
            output,
        )
        if match:
            return match.group(1)
    except Exception:
        pass

    try:
        output = run_adb(adb, serial, ["shell", "dumpsys", "window", "windows"])
        match = re.search(r"mCurrentFocus=.*?\s([A-Za-z0-9_.]+)/", output)
        if match:
            return match.group(1)
    except Exception:
        pass

    return None


def get_media(adb: str, serial: str | None) -> tuple[str | None, str | None, str]:
    try:
        output = run_adb(adb, serial, ["shell", "dumpsys", "media_session"], timeout=6)
    except Exception:
        return None, None, "unknown"

    state = "unknown"
    state_match = re.search(r"state=PlaybackState\s*\{state=(\d+)", output)
    if state_match:
        state = PLAYBACK_STATES.get(state_match.group(1), "unknown")

    title = None
    artist = None
    description = re.search(r"description=(.+)", output)
    if description:
        parts = [p.strip() for p in description.group(1).split(",")]
        if parts and parts[0]:
            title = parts[0]
        if len(parts) > 1 and parts[1] and parts[1] not in {"null", "Unknown"}:
            artist = parts[1]

    return title, artist, state


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Android status via ADB.")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL"))
    args = parser.parse_args()

    try:
        serial = pick_serial(args.adb, args.serial)
        if not serial:
            print("STATUS:BATTERY:NA|CHARGING:NA|MEDIA:NA|NA|unknown|MODEL:NA")
            return 1

        model = get_model(args.adb, serial)
        level, charging = get_battery(args.adb, serial)
        app = get_foreground_app(args.adb, serial)
        title, artist, media_state = get_media(args.adb, serial)

        battery = "NA" if level is None else str(level)
        charging_text = "NA" if charging is None else str(charging).lower()
        app_name = APP_NAMES.get(app or "", "NA")

        print(
            "STATUS:BATTERY:{battery}|CHARGING:{charging}"
            "|MEDIA:{title}|{artist}|{state}|MODEL:{model}"
            "|APP:{app}|APPNAME:{appname}".format(
                battery=battery,
                charging=charging_text,
                title=sanitize(title),
                artist=sanitize(artist),
                state=sanitize(media_state),
                model=sanitize(model),
                app=sanitize(app),
                appname=sanitize(app_name),
            )
        )
        return 0
    except FileNotFoundError:
        print(
            "STATUS:BATTERY:NA|CHARGING:NA|MEDIA:NA|NA|unknown|MODEL:NA",
            file=sys.stdout,
        )
        print("ADB not found. Install Android Platform Tools and add adb to PATH.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            "STATUS:BATTERY:NA|CHARGING:NA|MEDIA:NA|NA|unknown|MODEL:NA",
            file=sys.stdout,
        )
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
