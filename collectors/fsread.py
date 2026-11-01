"""Small read-only filesystem helpers shared by the collectors.

Only the reading half of the demo's ``atomic`` module lives here: collectors
never write outside ``run/``, so the atomic-write helpers would be dead code.
Every helper degrades to ``None``/a warning instead of raising, which is what
lets ``nekotrak_collector`` treat a missing or half-written NekoTrak file as a
null field rather than a failed device.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def read_text(
    path: str | os.PathLike[str], *, max_bytes: int = 0
) -> str | None:
    """Read UTF-8 text; a missing or unreadable file returns ``None``.

    ``max_bytes > 0`` reads only the tail of the file, which is what the
    collectors want for append-only logs.
    """

    target = Path(path)
    try:
        if not target.is_file():
            return None
        if max_bytes > 0 and target.stat().st_size > max_bytes:
            with target.open("rb") as handle:
                handle.seek(-max_bytes, os.SEEK_END)
                data = handle.read()
            return data.decode("utf-8", errors="replace")
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    except (UnicodeError, ValueError):
        return None


def read_json(
    path: str | os.PathLike[str], *, max_bytes: int = 4 * 1024 * 1024
) -> tuple[Any | None, str | None]:
    """Read a JSON file as ``(payload, warn)``.

    A missing file is not an error and returns ``(None, None)``; a corrupt or
    oversized file returns ``(None, reason)`` so the caller can degrade one
    field and keep collecting.
    """

    target = Path(path)
    try:
        if not target.is_file():
            return None, None
        size = target.stat().st_size
        if size > max_bytes:
            return None, f"JSON 文件过大（{size} 字节），已跳过：{target}"
        with target.open("r", encoding="utf-8") as handle:
            return json.load(handle), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败（{target.name}）：{exc.msg} @line {exc.lineno}"
    except OSError as exc:
        return None, f"JSON 读取失败（{target.name}）：{exc}"
    except ValueError as exc:
        return None, f"JSON 内容异常（{target.name}）：{exc}"
