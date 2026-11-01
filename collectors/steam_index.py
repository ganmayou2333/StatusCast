"""Steam 库索引：``libraryfolders.vdf`` + ``appmanifest_*.acf``。

只做三件事：

1. 从注册表 / 环境变量 / 常见安装位置发现 Steam 根目录；
2. 解析 ``libraryfolders.vdf`` 得到所有库目录，读取每个库的
   ``steamapps/appmanifest_*.acf`` 里的 ``name`` / ``installdir``；
3. 把"前台进程完整路径"匹配到某个游戏的安装目录，从而得到当前游戏名。

索引缓存 10 分钟。所有 IO 失败都降级为空索引 + ``warns``，不抛异常。
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path
from typing import Any

CACHE_TTL_SECONDS = 600.0

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"built_at": 0.0, "index": None}


# --------------------------------------------------------------------------
# VDF / ACF 解析（Valve KeyValues 文本格式的最小子集）
# --------------------------------------------------------------------------


def _tokenize_vdf(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char in " \t\r\n":
            i += 1
            continue
        if char == "/" and i + 1 < length and text[i + 1] == "/":
            newline = text.find("\n", i)
            i = length if newline < 0 else newline + 1
            continue
        if char in "{}":
            tokens.append(char)
            i += 1
            continue
        if char == '"':
            i += 1
            buffer: list[str] = []
            while i < length:
                current = text[i]
                if current == "\\" and i + 1 < length:
                    buffer.append(text[i + 1])
                    i += 2
                    continue
                if current == '"':
                    i += 1
                    break
                buffer.append(current)
                i += 1
            tokens.append("".join(buffer))
            continue
        start = i
        while i < length and text[i] not in ' \t\r\n"{}':
            i += 1
        if i > start:
            tokens.append(text[start:i])
        else:  # 防御未知字符，避免死循环
            i += 1
    return tokens


class _VdfParser:
    def __init__(self, tokens: list[str]) -> None:
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> str | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _next(self) -> str | None:
        value = self._peek()
        if value is not None:
            self._pos += 1
        return value

    def parse_object(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        while True:
            token = self._peek()
            if token is None or token == "}":
                if token == "}":
                    self._pos += 1
                return result
            key = self._next()
            if key is None:
                return result
            if key in "{}":
                continue
            value = self._peek()
            if value == "{":
                self._pos += 1
                result[key] = self.parse_object()
            elif value is None or value == "}":
                return result
            else:
                result[key] = self._next()


def parse_vdf(text: str) -> dict[str, Any]:
    """把 KeyValues 文本解析成嵌套 dict；非法输入返回空 dict。"""

    if not text:
        return {}
    try:
        return _VdfParser(_tokenize_vdf(text)).parse_object()
    except Exception:  # noqa: BLE001 - 解析器必须永不抛出
        return {}


# --------------------------------------------------------------------------
# Steam 根目录发现
# --------------------------------------------------------------------------


def _registry_candidates() -> list[Path]:
    found: list[Path] = []
    try:
        import winreg  # type: ignore[import-not-found]
    except Exception:  # 非 Windows
        return found
    probes = [
        (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
    ]
    for hive, subkey, value_name in probes:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, str) and value.strip():
            found.append(Path(value.strip()))
    return found


def _default_candidates() -> list[Path]:
    candidates = [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"C:\Program Files\Steam"),
    ]
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.append(Path(f"{drive}:\\Steam"))
        candidates.append(Path(f"{drive}:\\SteamLibrary"))
    return candidates


def discover_steam_root() -> Path | None:
    """按 env → 注册表 → 常见位置 的顺序找到 Steam 根目录。"""

    for env_name in ("STEAM_ROOT", "STEAM_PATH", "STEAM_HOME"):
        raw = os.environ.get(env_name)
        if raw:
            candidate = Path(raw)
            if (candidate / "steamapps").is_dir():
                return candidate
    for candidate in _registry_candidates():
        if (candidate / "steamapps").is_dir():
            return candidate
    for candidate in _default_candidates():
        try:
            if (candidate / "steamapps").is_dir():
                return candidate
        except OSError:
            continue
    return None


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------


class SteamIndex:
    """不可变的 Steam 应用索引。"""

    def __init__(
        self,
        root: Path | None,
        libraries: list[Path],
        entries: list[dict[str, Any]],
        warns: list[str],
        built_at: float | None = None,
    ) -> None:
        self.root = root
        self.libraries = libraries
        self.entries = entries
        self.warns = warns
        self.built_at = time.time() if built_at is None else built_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root) if self.root else None,
            "libraries": [str(path) for path in self.libraries],
            "appCount": len(self.entries),
            "builtAt": _iso_local(self.built_at),
            "warns": list(self.warns),
        }

    def match_path(self, exe_path: str | None) -> dict[str, Any] | None:
        """把可执行文件完整路径匹配到 ``steamapps/common/<installdir>``。"""

        if not exe_path:
            return None
        normalized = _norm_path(exe_path)
        best: dict[str, Any] | None = None
        best_len = -1
        for entry in self.entries:
            common = entry.get("commonPath")
            if not common:
                continue
            prefix = _norm_path(common)
            if not prefix:
                continue
            if normalized == prefix or normalized.startswith(prefix + os.sep):
                if len(prefix) > best_len:
                    best = entry
                    best_len = len(prefix)
        return best


def _norm_path(raw: str) -> str:
    try:
        return os.path.normcase(os.path.abspath(os.path.expandvars(str(raw))))
    except (OSError, ValueError):
        return ""


def _iso_local(timestamp: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")


def _read_library_paths(root: Path, warns: list[str]) -> list[Path]:
    libraries: list[Path] = [root]
    vdf_path = root / "steamapps" / "libraryfolders.vdf"
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warns.append(f"libraryfolders.vdf 不可读：{exc}")
        return libraries
    data = parse_vdf(text)
    folder_root = data.get("libraryfolders")
    if not isinstance(folder_root, dict):
        # 旧版 vdf 可能直接以数字为键
        folder_root = data if any(str(k).isdigit() for k in data) else {}
    raw_paths: list[str] = []
    for key, value in folder_root.items():
        if isinstance(value, dict):
            candidate = value.get("path") or value.get("Path")
            if isinstance(candidate, str):
                raw_paths.append(candidate)
        elif isinstance(value, str) and str(key).isdigit():
            raw_paths.append(value)
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_absolute():
            continue
        if path.is_dir() and path not in libraries:
            libraries.append(path)
    if len(libraries) <= 1:
        warns.append("libraryfolders.vdf 未解析到额外 Steam 库")
    return libraries


def _read_manifests(library: Path, warns: list[str]) -> list[dict[str, Any]]:
    steamapps = library / "steamapps"
    entries: list[dict[str, Any]] = []
    try:
        manifests = sorted(steamapps.glob("appmanifest_*.acf"))
    except OSError as exc:
        warns.append(f"无法枚举 appmanifest（{steamapps}）：{exc}")
        return entries
    for manifest in manifests:
        try:
            text = manifest.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warns.append(f"appmanifest 读取失败（{manifest.name}）：{exc}")
            continue
        data = parse_vdf(text)
        state = data.get("AppState")
        if not isinstance(state, dict):
            warns.append(f"appmanifest 结构异常：{manifest.name}")
            continue
        appid = str(state.get("appid") or "").strip()
        name = str(state.get("name") or "").strip()
        installdir = str(state.get("installdir") or "").strip()
        if not appid or not name:
            warns.append(f"appmanifest 缺少 appid/name：{manifest.name}")
            continue
        common_path = steamapps / "common" / installdir if installdir else None
        entries.append(
            {
                "appid": appid,
                "name": name,
                "installdir": installdir,
                "library": str(library),
                "manifest": str(manifest),
                "commonPath": str(common_path) if common_path else None,
            }
        )
    return entries


def build_index(
    root: Path | str | None = None,
    *,
    force: bool = False,
    now: float | None = None,
) -> SteamIndex:
    """构建（或复用缓存）Steam 索引。

    ``root`` 缺省时自动发现；显式传入便于测试。缓存键为根目录。
    """

    resolved_root = Path(root) if root is not None else discover_steam_root()
    cache_key = str(resolved_root) if resolved_root else "<none>"
    moment = time.time() if now is None else now
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if (
            not force
            and cached
            and moment - cached.built_at < CACHE_TTL_SECONDS
        ):
            return cached
    warns: list[str] = []
    libraries: list[Path] = []
    entries: list[dict[str, Any]] = []
    if resolved_root is None:
        warns.append("未发现 Steam 安装目录（注册表/环境变量均无结果）")
    elif not resolved_root.is_dir():
        warns.append(f"Steam 根目录不存在：{resolved_root}")
    else:
        libraries = _read_library_paths(resolved_root, warns)
        for library in libraries:
            entries.extend(_read_manifests(library, warns))
        if not entries:
            warns.append("Steam 库中未找到任何 appmanifest_*.acf")
    index = SteamIndex(resolved_root, libraries, entries, warns, built_at=moment)
    with _CACHE_LOCK:
        _CACHE[cache_key] = index
    return index


def get_index() -> SteamIndex:
    """取当前索引（10 分钟缓存）。"""

    return build_index()


def reset_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
