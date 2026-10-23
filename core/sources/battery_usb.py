"""USB battery source backed by pymobiledevice3."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.models import BatteryStatus
from core.sources.base import BatterySource

BATTERY_DOMAIN = "com.apple.mobile.battery"


class BatteryUSBError(RuntimeError):
    """Internal marker for classified battery failures."""


class BatteryUSB(BatterySource):
    name = "battery-usb"

    def __init__(self, serial: str | None = None, timeout: float = 20.0) -> None:
        self.serial = serial
        self.timeout = timeout
        self._logger = logging.getLogger(__name__)

    async def read(self) -> BatteryStatus:
        try:
            level, charging = await asyncio.wait_for(
                self._read_once(), timeout=self.timeout + 5
            )
        except TimeoutError:
            return self._error("读取 iPhone 电量超时，请确认设备已解锁并保持 USB 连接")
        except ModuleNotFoundError as exc:
            self._logger.debug("pymobiledevice3 dependency missing", exc_info=exc)
            return self._error("缺少 pymobiledevice3，请安装 requirements.txt")
        except Exception as exc:  # noqa: BLE001 - source must degrade, not crash
            message = self._friendly_error(exc)
            self._logger.debug("battery read failed: %s", message)
            return self._error(message)

        return BatteryStatus(
            level=level,
            is_charging=charging,
            updated_at=time.time(),
        )

    async def _read_once(self) -> tuple[int, bool]:
        from pymobiledevice3.exceptions import (  # noqa: PLC0415
            DeviceNotFoundError,
            MuxException,
            NoDeviceConnectedError,
            PairingError,
            PyMobileDevice3Exception,
        )
        from pymobiledevice3.lockdown import create_using_usbmux  # noqa: PLC0415

        try:
            async with await create_using_usbmux(
                serial=self.serial,
                connection_type="USB",
                pair_timeout=self.timeout,
            ) as lockdown:
                level_raw = await lockdown.get_value(
                    BATTERY_DOMAIN, "BatteryCurrentCapacity"
                )
                charging_raw = await lockdown.get_value(
                    BATTERY_DOMAIN, "BatteryIsCharging"
                )
        except (NoDeviceConnectedError, DeviceNotFoundError) as exc:
            raise BatteryUSBError(
                "未检测到 USB 连接的 iPhone。请连接数据线、解锁并点“信任”。"
            ) from exc
        except PairingError as exc:
            raise BatteryUSBError(
                "iPhone 尚未信任此电脑，请解锁后重新插拔并在弹窗中点“信任”。"
            ) from exc
        except MuxException as exc:
            raise BatteryUSBError(
                "无法连接 Apple Mobile Device Service (usbmuxd)，请安装 iTunes 并确认服务运行。"
            ) from exc
        except PyMobileDevice3Exception as exc:
            raise BatteryUSBError(f"读取 iPhone 电量失败: {exc}") from exc

        return _normalize_level(level_raw), _normalize_charging(charging_raw)

    def _error(self, message: str) -> BatteryStatus:
        return BatteryStatus(updated_at=time.time(), error=message)

    @staticmethod
    def _friendly_error(exc: Exception) -> str:
        if isinstance(exc, BatteryUSBError):
            return str(exc)
        if isinstance(exc, ValueError):
            return f"iPhone 返回了异常的电量数据: {exc}"
        return f"读取 iPhone 电量失败: {type(exc).__name__}: {exc}"


def _normalize_level(raw: Any) -> int:
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"BatteryCurrentCapacity 不是有效数字: {raw!r}")
    level = int(round(float(raw)))
    if not 0 <= level <= 100:
        raise ValueError(f"BatteryCurrentCapacity 超出 0-100: {raw!r}")
    return level


def _normalize_charging(raw: Any) -> bool:
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "charging"}
    return bool(raw)
