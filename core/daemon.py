"""Resident StatusCast daemon.

The daemon owns the long-lived media connection (BLE AMS) and polls the battery
at a lower rate. Each cycle it aggregates a Status and fans it out to every
enabled adapter. PowerShell then reads the snapshot adapter instead of
reconnecting to the iPhone on every display refresh.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# Allow both ``python -m core.daemon`` and ``python core/daemon.py``.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from adapters.base import Adapter
from adapters.factory import build_adapters
from core.aggregator import StatusAggregator
from core.config import Config, ConfigError
from core.console import configure_utf8_stdio
from core.lock import AlreadyRunningError, SingleInstance
from core.models import BatteryStatus, MediaStatus, Status
from core.sources.base import BatterySource, MediaSource
from core.sources.factory import build_battery_source, build_media_source

LOGGER = logging.getLogger("statuscast")


class Daemon:
    def __init__(
        self,
        config: Config,
        *,
        battery: BatterySource | None,
        media: MediaSource | None,
        adapters: list[Adapter],
        once: bool = False,
    ) -> None:
        self.config = config
        self.once = once
        self.battery = battery
        self.media = media
        self.aggregator = StatusAggregator()
        self.adapters = adapters
        self.stop_file = (
            config.resolve_path(config.app.stop_file)
            if config.app.stop_file
            else None
        )

    @classmethod
    def from_config(cls, config: Config, *, once: bool = False) -> "Daemon":
        return cls(
            config,
            battery=build_battery_source(config),
            media=build_media_source(config),
            adapters=build_adapters(config),
            once=once,
        )

    async def run(self) -> int:
        stop_event = asyncio.Event()
        if self.stop_file is not None:
            try:
                self.stop_file.unlink(missing_ok=True)
            except OSError:
                pass
        previous_handlers: dict[int, Any] = {}

        def request_stop(signum: int, _frame: Any) -> None:
            LOGGER.info("received signal %s, shutting down", signum)
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                previous_handlers[sig] = signal.signal(sig, request_stop)
            except (ValueError, OSError):
                pass

        media_task: asyncio.Task[None] | None = None
        if self.media is not None:
            media_task = asyncio.create_task(
                self.media.run(stop_event),
                name="media-source",
            )
            # Let the media task enter its run loop before a possible --once
            # shutdown, so diagnostics see the same lifecycle as production.
            await asyncio.sleep(0)

        battery_status = BatteryStatus(
            error=None if self.battery is not None else "电池源未启用"
        )
        last_battery_read = 0.0
        try:
            while not stop_event.is_set():
                if self._stop_requested():
                    LOGGER.info("stop requested via %s", self.stop_file)
                    stop_event.set()
                    break
                now = time.monotonic()
                if self.battery is not None and (
                    last_battery_read == 0.0
                    or now - last_battery_read >= self.config.battery.poll_interval
                ):
                    battery_status = await self.battery.read()
                    last_battery_read = now

                media_status = (
                    self.media.snapshot()
                    if self.media is not None
                    else MediaStatus(error="媒体源未启用")
                )
                status = self.aggregator.build(battery_status, media_status)
                await self._push_all(status)

                if self.once:
                    break

                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.config.app.poll_interval,
                    )
                except TimeoutError:
                    pass
        except KeyboardInterrupt:
            LOGGER.info("interrupted, shutting down")
        finally:
            stop_event.set()
            if media_task is not None:
                media_task.cancel()
                await asyncio.gather(media_task, return_exceptions=True)
            if self.media is not None:
                await self.media.close()
            if self.battery is not None:
                await self.battery.close()
            for adapter in self.adapters:
                try:
                    await adapter.close()
                except Exception:
                    LOGGER.exception("adapter close failed: %s", adapter.name)
            for sig, handler in previous_handlers.items():
                try:
                    signal.signal(sig, handler)
                except (ValueError, OSError):
                    pass
        return 0

    def _stop_requested(self) -> bool:
        if self.stop_file is None or not self.stop_file.exists():
            return False
        try:
            self.stop_file.unlink()
        except OSError:
            pass
        return True

    async def _push_all(self, status: Status) -> None:
        LOGGER.debug("status: %s", status.display_text())
        for adapter in self.adapters:
            try:
                await adapter.push(status)
            except Exception:
                LOGGER.exception("adapter push failed: %s", adapter.name)


def _configure_logging(level: str, log_path: Path | None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(
                RotatingFileHandler(
                    log_path,
                    maxBytes=2_000_000,
                    backupCount=3,
                    encoding="utf-8",
                )
            )
        except OSError as exc:
            print(f"无法写入日志文件 {log_path}: {exc}", file=sys.stderr)

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def _load_config(path: str) -> Config:
    try:
        return Config.load(path)
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    except OSError as exc:
        print(f"无法读取配置文件 {path}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="StatusCast 常驻核心")
    parser.add_argument("--config", default="config.toml", help="配置文件路径")
    parser.add_argument(
        "--once",
        action="store_true",
        help="只采集并推送一次，然后退出（用于调试）",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="向正在运行的 daemon 写入停止请求并退出",
    )
    args = parser.parse_args(argv)

    config = _load_config(args.config)
    if args.stop:
        if not config.app.stop_file:
            print("配置未启用 stop_file，无法发送停止请求", file=sys.stderr)
            return 2
        stop_path = config.resolve_path(config.app.stop_file)
        try:
            stop_path.parent.mkdir(parents=True, exist_ok=True)
            stop_path.write_text("stop\n", encoding="utf-8")
        except OSError as exc:
            print(f"无法写入停止请求 {stop_path}: {exc}", file=sys.stderr)
            return 2
        print(f"Stop requested: {stop_path}")
        return 0

    log_path = config.resolve_path(config.app.log_file) if config.app.log_file else None
    _configure_logging(config.app.log_level, log_path)

    lock = SingleInstance()
    try:
        lock.acquire()
    except AlreadyRunningError:
        LOGGER.warning("another StatusCast daemon is already running; exiting")
        return 0

    try:
        daemon = Daemon.from_config(config, once=args.once)
        LOGGER.info(
            "starting StatusCast (battery=%s, media=%s, adapters=%s)",
            config.battery.enabled,
            config.media.source if config.media.enabled else "disabled",
            ", ".join(adapter.name for adapter in daemon.adapters) or "none",
        )
        return asyncio.run(daemon.run())
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
