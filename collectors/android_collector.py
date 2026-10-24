"""Android collector plugin (ADB, no root required)."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

from .base import BaseCollector, CollectorError


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

# Foreground-app fields exposed by `dumpsys`. The activity manager fields are
# preferred because they describe the activity the user actually resumed; the
# window manager's mCurrentFocus is used as a fallback for OEM builds that omit
# or rename the activity fields.
_ACTIVITY_FOREGROUND_RE = re.compile(
    r"(?:topResumedActivity|mResumedActivity)\s*[=:]\s*[^\n]*?"
    r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)
_WINDOW_FOREGROUND_RE = re.compile(
    r"mCurrentFocus\s*=\s*[^\n]*?"
    r"\b([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/"
)

# `mCurrentFocus` can briefly point at the notification shade or a system
# dialog; those are not user apps and should not become the foreground app.
_SYSTEM_FOREGROUND_PACKAGES = {"android", "com.android.systemui"}


class AndroidCollector(BaseCollector):
    """Read Android battery, model, foreground app and best-effort media."""

    platform = "android"
    device_id = "android"

    def __init__(self, adb: str | None = None, serial: str | None = None) -> None:
        self.adb = adb or os.environ.get("ADB", "adb")
        self.serial = serial or os.environ.get("ANDROID_SERIAL")

    def get_data(self) -> dict[str, Any]:
        try:
            serial = self._pick_serial()
            if not serial:
                raise CollectorError("未检测到 Android 设备（adb devices 为空）")
            self.serial = serial

            model = self._get_model()
            level, charging = self._get_battery()
            app = self._get_foreground_app()
            title, artist, media_state = self._get_media()
        except CollectorError:
            raise
        except FileNotFoundError as exc:
            raise CollectorError(
                "ADB not found. Install Android Platform Tools and add adb to PATH."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CollectorError("ADB 命令超时，请确认手机已授权 USB 调试") from exc
        except Exception as exc:
            raise CollectorError(f"Android 采集失败：{exc}") from exc

        if level is None and model is None:
            raise CollectorError("未检测到 Android 设备")

        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": model,
            "batteryLevel": level,
            "isCharging": charging,
            "mediaTitle": title,
            "mediaArtist": artist,
            "mediaState": media_state,
            "foregroundApp": app,
            "foregroundAppName": APP_NAMES.get(app or ""),
            "error": None,
        }

    def _run_adb(self, args: list[str], timeout: int = 8) -> str:
        command = [self.adb]
        if self.serial:
            command += ["-s", self.serial]
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
            raise RuntimeError(
                result.stderr.strip() or f"adb exit code {result.returncode}"
            )
        return result.stdout

    def _pick_serial(self) -> str | None:
        if self.serial:
            return self.serial
        output = self._run_adb(["devices"])
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                return parts[0]
        return None

    def _get_model(self) -> str | None:
        model = self._run_adb(["shell", "getprop", "ro.product.model"]).strip()
        manufacturer = self._run_adb(
            ["shell", "getprop", "ro.product.manufacturer"]
        ).strip()
        if not model:
            return None
        if manufacturer and manufacturer.lower() not in model.lower():
            return f"{manufacturer} {model}"
        return model

    def _get_battery(self) -> tuple[int | None, bool | None]:
        output = self._run_adb(["shell", "dumpsys", "battery"])
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

    @staticmethod
    def _match_foreground_package(
        output: str | None, pattern: re.Pattern[str]
    ) -> str | None:
        if not output:
            return None
        match = pattern.search(output)
        if not match:
            return None
        package = match.group(1)
        if package in _SYSTEM_FOREGROUND_PACKAGES:
            return None
        return package

    def _get_foreground_app(self) -> str | None:
        """Cross-check two dumpsys views for the foreground package."""
        try:
            output = self._run_adb(["shell", "dumpsys", "activity", "activities"])
            package = self._match_foreground_package(output, _ACTIVITY_FOREGROUND_RE)
            if package:
                return package
        except Exception:
            pass

        try:
            output = self._run_adb(["shell", "dumpsys", "window", "displays"])
            package = self._match_foreground_package(output, _WINDOW_FOREGROUND_RE)
            if package:
                return package
        except Exception:
            pass

        return None

    def _get_media(self) -> tuple[str | None, str | None, str]:
        try:
            output = self._run_adb(["shell", "dumpsys", "media_session"], timeout=6)
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
            parts = [part.strip() for part in description.group(1).split(",")]
            if parts and parts[0]:
                title = parts[0]
            if len(parts) > 1 and parts[1] and parts[1] not in {"null", "Unknown"}:
                artist = parts[1]

        return title, artist, state
