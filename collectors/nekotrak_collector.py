"""NekoTrak collector: reads the app's own plaintext artefacts.

Data root is ``%APPDATA%\\NekoTrak`` (overridable with ``NEKOTRAK_HOME``):

* ``startup.log`` -- the last process session's phase timeline, version and
  uptime;
* ``com.bocchithedev.nekotrak\\logs\\NekoTrak.log`` -- TRACKER start/stop and
  ``custom_text_command_failed`` counters;
* ``users/<steamid>/pet-tales.txt`` -- JSONL pet events, last one wins;
* ``local.json`` -- Steam ID when the users directory is ambiguous.

Liveness is a double check: a heartbeat file rewritten within 60s *or* a live
``NekoTrak.exe`` process. Anything unreadable degrades to a null field plus a
``warns`` entry, so a corrupt log can never take the device down; only "not
running" raises :class:`CollectorError` and becomes the standard error entry.

Ported from the verified ``demo/plan-a`` collector. The demo-only
instrumentation (presence timings, window/config mirrors, MOCK fallback, CLI)
is intentionally not carried over.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import steam_index
from .base import BaseCollector, CollectorError
from .fsread import read_json, read_text
from .lan_probe import make_transport

APP_NAME = "NekoTrak"

_STARTUP_RE = re.compile(
    r"^(?P<ts>\S+)\s+\[Startup\]\s+pid=(?P<pid>\d+)\s+"
    r"version=(?P<version>\S+)\s+phase=(?P<phase>\d+)\s+"
    r"active_ms=(?P<active>\d+)\s+(?P<message>.*)$"
)
_LOG_LINE_RE = re.compile(
    r"^\[(?P<date>\d{4}-\d{2}-\d{2})\]\[(?P<time>\d{2}:\d{2}:\d{2})\]"
    r"\[(?P<level>[A-Z]+)\]\[(?P<target>[^\]]+)\]\s?(?P<message>.*)$"
)

PHASE_LABELS = {
    0: "进程启动",
    1: "初始化",
    2: "后端就绪",
    3: "就绪",
    5: "退出请求",
    6: "退出请求",
}

CATEGORY_LABELS = {
    "fight": "对战",
    "eat": "进食",
    "sleep": "睡觉",
    "play": "玩耍",
    "groom": "理毛",
    "explore": "探索",
    "gift": "礼物",
    "levelup": "升级",
    "hunt": "狩猎",
    "train": "训练",
    "idle": "发呆",
    "health": "健康",
    "unknown": "未知",
}

CATEGORY_EMOJI = {
    "fight": "⚔️",
    "eat": "🍖",
    "sleep": "😴",
    "play": "🧶",
    "groom": "🪮",
    "explore": "🌙",
    "gift": "🎁",
    "levelup": "✨",
    "hunt": "🐾",
    "train": "💪",
    "idle": "🫧",
    "health": "💚",
}

_LOCAL_TZ = datetime.now().astimezone().tzinfo or timezone.utc


# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------


def iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.astimezone().isoformat(timespec="milliseconds")


def _parse_iso(text: str) -> datetime | None:
    raw = text.strip()
    if not raw:
        return None
    # Python 的 fromisoformat 对多于 6 位的小数秒不一定稳，先裁剪到 6 位。
    raw = re.sub(r"(\.\d{6})\d+", r"\1", raw)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LOCAL_TZ)
    return parsed


def _parse_log_timestamp(date_text: str, time_text: str) -> datetime | None:
    try:
        parsed = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=_LOCAL_TZ)


def resolve_home(home: str | os.PathLike[str] | None = None) -> Path:
    """解析 NekoTrak 数据根。

    优先显式参数 → ``NEKOTRAK_HOME`` → ``%APPDATA%\\NekoTrak``。
    """

    if home is not None:
        return Path(home)
    override = os.environ.get("NEKOTRAK_HOME")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / "AppData" / "Roaming" / APP_NAME


# --------------------------------------------------------------------------
# startup.log
# --------------------------------------------------------------------------


def parse_startup_log(
    text: str | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """解析 startup.log，返回最后一次进程会话的阶段时间线。"""

    moment = now or datetime.now().astimezone()
    result: dict[str, Any] = {
        "entries": [],
        "session": [],
        "version": None,
        "pid": None,
        "online": False,
        "startedAt": None,
        "lastLogAt": None,
        "uptimeSec": None,
        "phases": [],
        "ready": False,
        "warns": [],
    }
    if not text:
        result["warns"].append("startup.log 缺失或不可读")
        return result

    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        match = _STARTUP_RE.match(line.strip())
        if not match:
            continue
        at = _parse_iso(match.group("ts"))
        if at is None:
            continue
        entries.append(
            {
                "at": at,
                "pid": int(match.group("pid")),
                "version": match.group("version"),
                "phase": int(match.group("phase")),
                "activeMs": int(match.group("active")),
                "message": (match.group("message") or "").strip(),
            }
        )
    if not entries:
        result["warns"].append("startup.log 中未解析到 [Startup] 行")
        return result

    last_pid = entries[-1]["pid"]
    session: list[dict[str, Any]] = []
    for entry in reversed(entries):
        if entry["pid"] != last_pid:
            break
        session.append(entry)
    session.reverse()

    phases = []
    for entry in session:
        label = PHASE_LABELS.get(entry["phase"], f"阶段 {entry['phase']}")
        message = entry["message"]
        if entry["phase"] == 1 and "profile and application state ready" in message:
            label = "配置就绪"
        phases.append(
            {
                "phase": entry["phase"],
                "at": iso(entry["at"]),
                "label": label,
                "message": message,
            }
        )

    first = session[0]
    last = session[-1]
    ready = any(entry["phase"] == 3 for entry in session)
    online = last["phase"] in (1, 2, 3)
    if online:
        uptime = max(0.0, (moment - first["at"]).total_seconds())
    else:
        uptime = max(0.0, (last["activeMs"] - first["activeMs"]) / 1000.0)

    result.update(
        {
            "entries": entries,
            "session": session,
            "version": last["version"],
            "pid": last["pid"],
            "online": online,
            "startedAt": iso(first["at"]),
            "lastLogAt": iso(last["at"]),
            "uptimeSec": int(uptime),
            "phases": phases,
            "ready": ready,
        }
    )
    return result


# --------------------------------------------------------------------------
# NekoTrak.log（TRACKER + presence 超时）
# --------------------------------------------------------------------------


def parse_tracker_log(text: str | None) -> dict[str, Any]:
    """解析 ``NekoTrak.log``：TRACKER 状态 + custom_text 超时统计。"""

    result: dict[str, Any] = {
        "trackerActive": False,
        "trackerStartedAt": None,
        "trackerStoppedAt": None,
        "customTextFailures": 0,
        "customTextElapsedMs": [],
        "lastCustomTextFailureAt": None,
        "lastCustomTextSource": None,
        "suppressedCount": 0,
        "logLines": 0,
        "warns": [],
    }
    if not text:
        result["warns"].append("NekoTrak.log 缺失或不可读")
        return result

    last_start: datetime | None = None
    last_stop: datetime | None = None
    failures = 0
    elapsed: list[float] = []
    last_failure_at: datetime | None = None
    last_source: str | None = None
    suppressed = 0
    lines = 0

    for line in text.splitlines():
        lines += 1
        upper = line
        if "[TRACKER]" in upper:
            stamp = _line_stamp(line)
            if "All monitors started" in upper:
                last_start = stamp
            elif "Sent stop signal" in upper:
                last_stop = stamp
        if "custom_text_command_failed" in line:
            failures += 1
            stamp = _line_stamp(line)
            if stamp:
                last_failure_at = stamp
            payload = _extract_json(line)
            if isinstance(payload, dict):
                value = payload.get("elapsed_ms")
                if isinstance(value, (int, float)):
                    elapsed.append(float(value))
                if isinstance(payload.get("suppressed_count"), int):
                    suppressed += int(payload["suppressed_count"])
                if isinstance(payload.get("source"), str):
                    last_source = payload["source"]

    active = False
    if last_start is not None:
        active = last_stop is None or last_start > last_stop
    elif last_stop is not None:
        active = False

    result.update(
        {
            "trackerActive": bool(active) if (last_start or last_stop) else False,
            "trackerStartedAt": iso(last_start),
            "trackerStoppedAt": iso(last_stop),
            "customTextFailures": failures,
            "customTextElapsedMs": elapsed[-200:],
            "lastCustomTextFailureAt": iso(last_failure_at),
            "lastCustomTextSource": last_source,
            "suppressedCount": suppressed,
            "logLines": lines,
        }
    )
    return result


def _line_stamp(line: str) -> datetime | None:
    match = _LOG_LINE_RE.match(line)
    if not match:
        return None
    return _parse_log_timestamp(match.group("date"), match.group("time"))


def _extract_json(line: str) -> Any | None:
    start = line.find("{")
    end = line.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(line[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


# --------------------------------------------------------------------------
# 宠物事件流
# --------------------------------------------------------------------------


def summarize_pet_event(event: dict[str, Any]) -> str:
    category = str(event.get("category") or "unknown")
    label = CATEGORY_LABELS.get(category, category)
    cat_name = _clean_name(event.get("catName"))
    other = _clean_name(event.get("otherCatName"))

    if category == "fight":
        parts = [cat_name or "猫咪"]
        if other:
            parts.append(f"与 {other} 对战")
        rounds = event.get("rounds")
        if isinstance(rounds, int) and rounds > 0:
            parts.append(f"{rounds} 回合")
        winner = _clean_name(event.get("winnerName"))
        if winner:
            parts.append(f"· {winner} 获胜")
        return " ".join(parts)

    if category in {"eat", "play", "groom", "explore", "sleep"}:
        who = cat_name or "宠物"
        return f"{who} {label} · {event.get('textKey') or ''}".strip()

    who = cat_name or "宠物"
    return f"{who} {label} · {event.get('textKey') or '无文本键'}"


def _clean_name(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_pet_tales(text: str | None) -> dict[str, Any]:
    """读取 JSONL 宠物事件流，取最后一条有效事件。"""

    result: dict[str, Any] = {
        "name": None,
        "lastEvent": None,
        "eventCount": 0,
        "warns": [],
    }
    if not text:
        result["warns"].append("pet-tales.txt 缺失或不可读")
        return result

    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(payload, dict):
            events.append(payload)
    if not events:
        result["warns"].append("pet-tales.txt 中没有有效 JSONL 事件")
        return result

    event = events[-1]
    occurred_at = None
    occurred_ms = event.get("occurredAt")
    if isinstance(occurred_ms, (int, float)):
        try:
            occurred_at = datetime.fromtimestamp(float(occurred_ms) / 1000.0).astimezone()
        except (OverflowError, OSError, ValueError):
            occurred_at = None

    category = str(event.get("category") or "unknown")
    mood = event.get("moodDelta")
    summary = summarize_pet_event(event)
    result.update(
        {
            "name": _clean_name(event.get("catName")),
            "eventCount": len(events),
            "lastEvent": {
                "at": iso(occurred_at),
                "category": category,
                "categoryLabel": CATEGORY_LABELS.get(category, category),
                "summary": summary,
                "moodDelta": mood if isinstance(mood, (int, float)) else None,
                "experience": event.get("experience")
                if isinstance(event.get("experience"), (int, float))
                else None,
                "textKey": event.get("textKey"),
            },
            "emoji": _pet_emoji(category, mood),
        }
    )
    return result


def _pet_emoji(category: str, mood: Any) -> str:
    base = CATEGORY_EMOJI.get(category, "🐱")
    if isinstance(mood, (int, float)):
        if mood <= -2:
            return base + "😾"
        if mood >= 2:
            return base + "😸"
    return base


# --------------------------------------------------------------------------
# 前台进程与当前游戏
# --------------------------------------------------------------------------


def get_foreground_process() -> dict[str, Any] | None:
    """用 ctypes 读取前台窗口进程完整路径。非 Windows / 失败返回 ``None``。"""

    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:  # noqa: BLE001
        return None
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError:
        return None

    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            ok = kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            )
            if not ok:
                return None
            exe_path = buffer.value
        finally:
            kernel32.CloseHandle(handle)
        if not exe_path:
            return None
        return {
            "pid": int(pid.value),
            "exePath": exe_path,
            "exeName": Path(exe_path).name,
        }
    except Exception:  # noqa: BLE001 - 采集不得中断
        return None


def resolve_current_game(
    foreground: dict[str, Any] | None = None,
    index: steam_index.SteamIndex | None = None,
) -> dict[str, Any]:
    """前台进程 × Steam 索引 → 当前游戏。"""

    fg = foreground if foreground is not None else get_foreground_process()
    try:
        steam = index if index is not None else steam_index.get_index()
    except Exception as exc:  # noqa: BLE001
        return {
            "game": None,
            "appid": None,
            "foreground": fg,
            "steam": {"warns": [f"Steam 索引失败：{exc}"]},
            "reason": "Steam 索引不可用",
        }

    info: dict[str, Any] = {
        "game": None,
        "appid": None,
        "foreground": fg,
        "steam": steam.to_dict(),
        "reason": None,
    }
    if fg is None:
        info["reason"] = "无法读取前台窗口进程"
        return info

    match = steam.match_path(fg.get("exePath"))
    if match:
        info["game"] = match.get("name")
        info["appid"] = match.get("appid")
        info["reason"] = "matched-steam-common-path"
        return info

    # 索引缺 manifest 时，至少从 steamapps/common/<dir> 推断目录名
    normalized = os.path.normcase(os.path.abspath(fg.get("exePath") or ""))
    marker = f"{os.sep}steamapps{os.sep}common{os.sep}"
    lowered = normalized.lower()
    if marker in lowered:
        tail = normalized[lowered.index(marker) + len(marker) :]
        folder = tail.split(os.sep)[0]
        if folder:
            info["game"] = folder
            info["reason"] = "inferred-from-common-folder"
            return info
    info["reason"] = "前台进程不在 Steam 游戏目录"
    return info


# --------------------------------------------------------------------------
# Liveness: heartbeat file or a live process
# --------------------------------------------------------------------------

# NekoTrak rewrites this file while it runs; its mtime is the cheapest
# "is it still there" signal and, unlike startup.log, it cannot go stale after
# a crash because nothing rewrites it once the process is gone.
HEARTBEAT_FILE = "tracker-heartbeat.json"

# The daemon refreshes every 30s, so 60s tolerates exactly one missed beat
# while still turning a killed process into an error within the contract's
# 30s-to-60s window.
HEARTBEAT_FRESH_SECONDS = 60

# Windows: run tasklist without flashing a console window.
_CREATE_NO_WINDOW = 0x08000000

# Fields of the additive ``nekotrak`` object. Asserted against
# schema/status-contract.json by tests/test_contract.py, so adding a key here
# without editing the contract fails the suite.
NEKOTRAK_FIELDS: tuple[str, ...] = (
    "version",
    "source",
    "uptimeSec",
    "startedAt",
    "lastLogAt",
    "trackerActive",
    "trackerStartedAt",
    "trackerStoppedAt",
    "currentGame",
    "currentGameAppId",
    "pet",
    "pomodoro",
    "warns",
)


def _pick_steam_id(home: Path, local: Any) -> str | None:
    """Newest ``users/<steamid>`` (or the id written in ``local.json``)."""

    if isinstance(local, dict):
        raw = local.get("steam_id") or local.get("steamId")
        if isinstance(raw, (str, int)) and str(raw).strip():
            return str(raw).strip()
    users_dir = home / "users"
    try:
        candidates = [item for item in users_dir.iterdir() if item.is_dir()]
    except OSError:
        return None
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0].name


def _version_from_log(log_text: str | None) -> str | None:
    if not log_text:
        return None
    match = re.search(r"version=(\d+\.\d+\.\d+)", log_text)
    return match.group(1) if match else None


def heartbeat_fresh(
    home: Path, *, now: datetime | None = None, seconds: int = HEARTBEAT_FRESH_SECONDS
) -> bool:
    """True when the heartbeat file was written within ``seconds``."""

    moment = now or datetime.now().astimezone()
    try:
        mtime = (home / HEARTBEAT_FILE).stat().st_mtime
    except OSError:
        return False
    return abs(moment.timestamp() - mtime) <= seconds


def process_running() -> bool:
    """True when a ``NekoTrak.exe`` process exists in the process table.

    Read-only and stdlib-only: ``tasklist`` ships with Windows and needs no
    elevation for a name filter. Any failure is "not running", never an
    exception, because liveness must not take the collector down.
    """

    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq NekoTrak.exe", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "nekotrak.exe" in (completed.stdout or "").lower()


def is_running(
    home: Path,
    *,
    now: datetime | None = None,
    process_check: Any = None,
    seconds: int = HEARTBEAT_FRESH_SECONDS,
) -> bool:
    """Liveness double check: a fresh heartbeat *or* a live process.

    ``process_check`` is injectable so tests never depend on whether the real
    NekoTrak happens to be running on the machine executing them.
    """

    check = process_check or process_running
    return heartbeat_fresh(home, now=now, seconds=seconds) or bool(check())


def available(home: str | os.PathLike[str] | None = None) -> bool:
    """True when NekoTrak has ever run here, i.e. its data root exists.

    A machine that never ran NekoTrak gets no device row at all instead of a
    permanently-red ``NekoTrak 未运行`` entry, matching how an absent
    ``Consoles``/``BleDevices`` section produces no rows.
    """

    try:
        return resolve_home(home).is_dir()
    except OSError:
        return False


# --------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------


def collect(
    home: str | os.PathLike[str] | None = None,
    *,
    now: datetime | None = None,
    resolve_steam: bool = True,
    process_check: Any = None,
) -> dict[str, Any]:
    """Read one NekoTrak snapshot and return the ``nekotrak`` object payload.

    Raises :class:`CollectorError` when NekoTrak is not running, which is the
    dispatcher's signal to emit a standard error entry. Every other problem --
    missing file, corrupt JSON, drifted log format -- degrades that one field
    to ``None`` and appends to ``warns``.
    """

    moment = now or datetime.now().astimezone()
    home_path = resolve_home(home)
    warns: list[str] = []
    readable = False

    startup_text = read_text(home_path / "startup.log")
    readable = readable or startup_text is not None
    startup = parse_startup_log(startup_text, now=moment)
    warns.extend(startup["warns"])

    log_path = home_path / "com.bocchithedev.nekotrak" / "logs" / "NekoTrak.log"
    log_text = read_text(log_path)
    readable = readable or log_text is not None
    tracker = parse_tracker_log(log_text)
    warns.extend(tracker["warns"])

    local_data, local_warn = read_json(home_path / "local.json")
    if local_warn:
        warns.append(local_warn)
    steam_id = _pick_steam_id(home_path, local_data)
    user_dir = home_path / "users" / steam_id if steam_id else None

    pet_text = read_text(user_dir / "pet-tales.txt") if user_dir is not None else None
    readable = readable or pet_text is not None
    pet = parse_pet_tales(pet_text)
    if user_dir is None:
        warns.append("未找到 users/<steamid> 目录")
    elif not user_dir.is_dir():
        # The Steam ID is known but its data directory is gone (fresh login or
        # a deleted profile): say which one instead of only "no events".
        warns.append(f"users/{steam_id} 目录不存在")
    else:
        warns.extend(pet["warns"])

    if not readable:
        raise CollectorError("NekoTrak 未运行")
    if not is_running(home_path, now=moment, process_check=process_check):
        raise CollectorError("NekoTrak 未运行")

    game_info: dict[str, Any] = {
        "game": None,
        "appid": None,
        "foreground": None,
        "reason": "未采集",
    }
    if resolve_steam:
        try:
            game_info = resolve_current_game()
        except Exception as exc:  # noqa: BLE001 - one field must not fail the device
            warns.append(f"当前游戏解析失败：{exc}")

    if game_info.get("foreground") is None:
        warns.append("未能读取前台窗口进程（可能无交互桌面或权限不足）")

    return {
        "version": startup["version"] or _version_from_log(log_text),
        "source": "L1",
        "uptimeSec": startup["uptimeSec"],
        "startedAt": startup["startedAt"],
        "lastLogAt": startup["lastLogAt"] or tracker["lastCustomTextFailureAt"],
        "trackerActive": bool(tracker["trackerActive"]),
        "trackerStartedAt": tracker["trackerStartedAt"],
        "trackerStoppedAt": tracker["trackerStoppedAt"],
        "currentGame": game_info.get("game"),
        "currentGameAppId": game_info.get("appid"),
        "pet": {
            "name": pet["name"],
            "lastEvent": pet["lastEvent"],
            "emoji": pet.get("emoji"),
            "eventCount": pet["eventCount"],
        },
        "pomodoro": None,
        "warns": warns,
        # Device-level projections, stripped by NekoTrakCollector before the
        # object is attached to the entry (the contract governs that key set).
        "foregroundApp": (game_info.get("foreground") or {}).get("exeName"),
    }


class NekoTrakCollector(BaseCollector):
    """The NekoTrak desktop app running on this PC.

    Treated as its own device (``platform="windows"``) so the existing
    consumers -- the PS1 one-liner, the widget, the daemon -- pick it up with
    no changes: it is just another row in ``devices``.
    """

    platform = "windows"
    device_id = "nekotrak"

    def __init__(
        self,
        entry: Any = None,
        *,
        home: str | os.PathLike[str] | None = None,
        now: datetime | None = None,
        process_check: Any = None,
        resolve_steam: bool = True,
    ) -> None:
        if entry is not None and not isinstance(entry, dict):
            raise CollectorError("配置项必须是对象")
        self._home = home
        self._now = now
        self._process_check = process_check
        self._resolve_steam = resolve_steam

    def get_data(self) -> dict[str, Any]:
        data = collect(
            self._home,
            now=self._now,
            resolve_steam=self._resolve_steam,
            process_check=self._process_check,
        )
        version = data.get("version")
        last_seen = int((self._now or datetime.now().astimezone()).timestamp())
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": f"NekoTrak {version}" if version else "NekoTrak",
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": data.get("foregroundApp"),
            "foregroundAppName": data.get("currentGame"),
            "nekotrak": {name: data[name] for name in NEKOTRAK_FIELDS},
            # The "channel" here is the local process itself; there is no
            # remote transport to fall back to.
            "transport": make_transport(
                "process",
                degraded=False,
                online=True,
                last_seen=last_seen,
                detail="nekotrak",
            ),
            "error": None,
        }
