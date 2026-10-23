"""Apple Media Service media source implemented with bleak.

This is the real (long-running) AMS integration. The standalone
``scripts/probe_ams.py`` is kept as a diagnostic tool that exercises the same
protocol with a simpler control flow.

Windows notes:
- AMS requires an authenticated BLE link. If Windows is not bonded to the
  iPhone, ``start_notify`` fails with ``Insufficient Authentication``. Set
  ``media.pair = true`` in config.toml and confirm the pairing prompt on the
  iPhone, or pair it first in Windows Settings.
- AMS is not guaranteed to be advertised/present. The source rescans and
  reconnects, and reports a degraded MediaStatus instead of crashing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from core.models import MediaStatus
from core.sources.ams_protocol import (
    AMS_SERVICE,
    AMS_UPDATE,
    PLAYER_REGISTRATION,
    TRACK_REGISTRATION,
    AmsMediaState,
    parse_entity_update,
)
from core.sources.base import MediaSource


class AmsAuthRequiredError(RuntimeError):
    """AMS is present but the BLE link has not been authenticated/bonded."""


class AmsMediaSource(MediaSource):
    name = "media-ams"

    def __init__(
        self,
        *,
        address: str | None = None,
        pair: bool = False,
        scan_timeout: float = 8.0,
        retry_interval: float = 10.0,
        connect_timeout: float = 20.0,
    ) -> None:
        self.address = address
        self.pair = pair
        self.scan_timeout = scan_timeout
        self.retry_interval = retry_interval
        self.connect_timeout = connect_timeout

        self._state = AmsMediaState()
        self._error: str | None = None
        self._device_model: str = "?"
        self._logger = logging.getLogger(__name__)

    def snapshot(self) -> MediaStatus:
        return self._state.to_status(error=self._error)

    async def run(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            client = None
            try:
                target = await self._connect_first_ams()
                if target is None:
                    await self._wait(stop_event)
                    continue
                client, disconnected, address, model = target
                self._device_model = model
                self._error = None
                self._state = AmsMediaState()
                self._logger.info("AMS connected: %s (%s)", address, model)
                await self._listen(client, disconnected, stop_event)
            except asyncio.CancelledError:
                raise
            except AmsAuthRequiredError as exc:
                self._error = (
                    "AMS 需要 BLE 配对：请把 config.toml 的 media.pair 设为 true，"
                    "在 iPhone 上确认配对，或先在 Windows 设置中配对"
                )
                self._logger.warning("%s (%s)", self._error, exc)
            except Exception as exc:  # noqa: BLE001 - keep the daemon alive
                self._error = f"AMS 连接失败: {type(exc).__name__}: {exc}"
                self._logger.warning(self._error)
            finally:
                if client is not None:
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except Exception:
                        pass
            if not stop_event.is_set():
                await self._wait(stop_event)

    async def _connect_first_ams(
        self,
    ) -> tuple[Any, asyncio.Event, str, str] | None:
        from bleak import BleakClient, BleakScanner  # noqa: PLC0415
        from bleak.exc import BleakError  # noqa: PLC0415

        self._logger.debug("scanning for Apple BLE devices")
        discovered = await BleakScanner.discover(
            timeout=self.scan_timeout,
            return_adv=True,
        )
        candidates: list[tuple[Any, Any]] = []
        for device, advertisement in discovered.values():
            if 0x004C not in (advertisement.manufacturer_data or {}):
                continue
            if self.address and device.address.upper() != self.address.upper():
                continue
            candidates.append((device, advertisement))

        candidates.sort(
            key=lambda item: item[1].rssi if item[1].rssi is not None else -999,
            reverse=True,
        )

        if self.address and not candidates:
            self._error = f"未找到指定 BLE 地址 {self.address}，请确认 iPhone 蓝牙已开启且已解锁"
            self._logger.warning(self._error)
            return None

        last_error: str | None = None
        for device, advertisement in candidates:
            client = None
            disconnected = asyncio.Event()
            try:
                client, disconnected = await self._connect_candidate(device)
                if client.services.get_service(AMS_SERVICE) is None:
                    self._logger.debug(
                        "device %s does not expose AMS; skipping", device.address
                    )
                    continue

                model = await self._read_model(client)
                if self.pair:
                    # Pair only after AMS is confirmed, so we do not prompt for
                    # unrelated Apple devices during a broad scan.
                    self._logger.info(
                        "AMS found on %s; reconnecting with pairing enabled",
                        device.address,
                    )
                    await client.disconnect()
                    client = None
                    client, disconnected = await self._connect_candidate(
                        device, pair=True
                    )
                    if client.services.get_service(AMS_SERVICE) is None:
                        raise RuntimeError("配对后 AMS 服务消失")

                return client, disconnected, device.address, model
            except AmsAuthRequiredError as exc:
                last_error = str(exc)
                self._logger.debug("authentication required: %s", exc)
            except BleakError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                self._logger.debug("BLE connect failed for %s: %s", device.address, exc)
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
                self._logger.debug("connect failed for %s: %s", device.address, exc)
            finally:
                if client is not None:
                    try:
                        if client.is_connected:
                            await client.disconnect()
                    except Exception:
                        pass

        self._error = last_error or "附近没有找到暴露 AMS 的 iPhone"
        self._logger.warning(self._error)
        return None

    async def _connect_candidate(
        self, device: Any, *, pair: bool = False
    ) -> tuple[Any, asyncio.Event]:
        from bleak import BleakClient  # noqa: PLC0415

        disconnected = asyncio.Event()

        def on_disconnected(_client: Any) -> None:
            disconnected.set()

        client = BleakClient(
            device,
            timeout=self.connect_timeout,
            pair=pair,
            disconnected_callback=on_disconnected,
        )
        await client.connect()
        if not client.is_connected:
            raise RuntimeError("BLE client reported disconnected after connect()")
        return client, disconnected

    async def _read_model(self, client: Any) -> str:
        try:
            raw = await client.read_gatt_char("2a24")
        except Exception:
            return "?"
        return bytes(raw).decode("utf-8", errors="replace").strip() or "?"

    async def _listen(
        self,
        client: Any,
        disconnected: asyncio.Event,
        stop_event: asyncio.Event,
    ) -> None:
        from bleak.exc import BleakGATTProtocolError  # noqa: PLC0415

        def on_update(_characteristic: Any, data: bytearray) -> None:
            try:
                note = parse_entity_update(data)
            except ValueError as exc:
                self._logger.debug("invalid AMS notification %s: %s", bytes(data).hex(), exc)
                return
            self._state.apply(note)
            self._logger.debug(
                "AMS %s = %r (flags=0x%02x)", note.name, note.value, note.flags
            )

        try:
            await client.start_notify(AMS_UPDATE, on_update)
        except BleakGATTProtocolError as exc:
            if "Insufficient Authentication" in str(exc):
                raise AmsAuthRequiredError(str(exc)) from exc
            raise

        await client.write_gatt_char(AMS_UPDATE, PLAYER_REGISTRATION, response=True)
        await client.write_gatt_char(AMS_UPDATE, TRACK_REGISTRATION, response=True)
        self._logger.info("AMS subscribed (player + track attributes)")

        stop_task = asyncio.create_task(stop_event.wait())
        disconnect_task = asyncio.create_task(disconnected.wait())
        try:
            done, pending = await asyncio.wait(
                {stop_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if disconnect_task in done and not stop_event.is_set():
                self._error = "AMS 连接已断开，正在重连..."
                self._logger.warning(self._error)
        finally:
            for task in (stop_task, disconnect_task):
                if not task.done():
                    task.cancel()

    async def _wait(self, stop_event: asyncio.Event) -> None:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=self.retry_interval)
        except TimeoutError:
            pass
