"""Single-instance guard for the daemon.

The Windows implementation uses a named kernel mutex. It is released
automatically when the process dies, so a crashed daemon never leaves a stale
lock behind.
"""

from __future__ import annotations

import ctypes
import os
import tempfile
from pathlib import Path
from typing import IO, Any

MUTEX_NAME = r"Local\StatusCastDaemon"
ERROR_ALREADY_EXISTS = 183


class AlreadyRunningError(RuntimeError):
    """Another StatusCast daemon already owns the lock."""


class SingleInstance:
    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle: Any = None
        self._file: IO[str] | None = None

    def acquire(self) -> None:
        if os.name == "nt":
            self._acquire_windows()
        else:  # pragma: no cover - StatusCast targets Windows
            self._acquire_posix()

    def release(self) -> None:
        if os.name == "nt":
            if self._handle:
                ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
                self._handle = None
            return
        if self._file is not None:  # pragma: no cover - Windows target
            import fcntl

            try:
                fcntl.flock(self._file, fcntl.LOCK_UN)
            finally:
                self._file.close()
                self._file = None

    def _acquire_windows(self) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_bool,
            ctypes.c_wchar_p,
        ]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.CreateMutexW(None, False, self.name)
        last_error = ctypes.get_last_error()
        if not handle:
            raise OSError(last_error, "CreateMutexW failed")
        if last_error == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise AlreadyRunningError("StatusCast daemon is already running")
        self._handle = handle

    def _acquire_posix(self) -> None:  # pragma: no cover - Windows target
        import fcntl

        path = Path(tempfile.gettempdir()) / "statuscast.lock"
        handle = path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise AlreadyRunningError("StatusCast daemon is already running") from exc
        self._file = handle
