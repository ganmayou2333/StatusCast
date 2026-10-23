"""Console helpers shared by command-line entry points."""

from __future__ import annotations

import sys


def configure_utf8_stdio() -> None:
    """Force UTF-8 on stdout/stderr so emoji survive Windows pipes.

    On Windows, redirected Python output often defaults to the ANSI code page
    (e.g. cp936), which cannot encode the battery/media emoji. Reconfiguring the
    text streams is safe on Python 3.7+ and a no-op where unsupported.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
