"""BLE peripheral collector (BleDevices): explicit whitelist, no discovery UI.

Each configured entry names one Bluetooth LE peripheral either by address or
by advertised-name substring. Every refresh:

1. scans once for ``SCAN_TIMEOUT_SECONDS`` (results cached for
   ``SCAN_CACHE_TTL_SECONDS`` so several BLE entries share one scan);
2. matches the entry against the scan cache (address first, then
   ``local_name``/``name`` substring -- case-insensitive);
3. connects, reads the GATT Battery Service (``0x180F``/``0x2A19``) and/or
   subscribes to the Heart Rate Measurement characteristic
   (``0x180D``/``0x2A37``), then disconnects.

A peripheral that is not advertising is a *normal* entry (``error`` stays
``None``; ``ble.statusText`` says ``未发现``). A peripheral that is seen but
cannot be connected still proves liveness, so the entry is online with
``transport.degraded=True`` and ``ble.statusText`` ``已发现未连接``. Only
invalid configuration and a missing ``bleak`` install raise
:class:`CollectorError`; the latter is raised lazily on first use so a machine
without the BLE stack keeps every other collector working.

bleak 3.0.2 specifics this module relies on:

- ``BleakScanner.discover(timeout=..., return_adv=True)`` returns
  ``{address: (BLEDevice, AdvertisementData)}`` (a plain list form is also
  tolerated);
- ``AdvertisementData`` carries ``local_name`` and ``rssi``;
- ``BleakClient(address, timeout=...)`` exposes async ``connect`` /
  ``disconnect`` / ``read_gatt_char`` / ``start_notify`` / ``stop_notify``.

bleak's Windows/WinRT backend is unreliable with concurrent event loops in one
process, so every scan and every connect is serialised behind a module lock.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from .base import BaseCollector, CollectorError
from .lan_probe import make_transport

# Scanning: how long one shared scan listens, how long its result is reused.
SCAN_TIMEOUT_SECONDS = 4.0
SCAN_CACHE_TTL_SECONDS = 10.0

# Per-device budgets: connect, one GATT read, and the heart-rate wait bound.
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 1.0
DEFAULT_HEART_RATE_WAIT_SECONDS = 3.0
MIN_HEART_RATE_WAIT_SECONDS = 1.0
MAX_HEART_RATE_WAIT_SECONDS = 8.0

# GATT Battery Service / Heart Rate Service (Bluetooth SIG assigned numbers).
BATTERY_LEVEL_CHARACTERISTIC_UUID = "00002a19-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_CHARACTERISTIC_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Config vocabulary for the ``services`` list.
VALID_SERVICES: tuple[str, ...] = ("battery", "heart_rate")
DEFAULT_SERVICES: tuple[str, ...] = ("battery",)

# Human-facing status text for the two non-success, non-error states.
STATUS_NOT_CONNECTED = "已发现未连接"
STATUS_NOT_FOUND = "未发现"

# Fixed text required by the interface contract.
MISSING_DEPENDENCY_MESSAGE = "缺少 BLE 依赖：bleak"

# bleak/WinRT may not run two event loops in one process concurrently.
_SCAN_LOCK = threading.Lock()
_BLE_LOCK = threading.Lock()

# Shared, TTL-bounded scan result: {UPPERCASE_ADDRESS: info-dict}.
_SCAN_CACHE: dict[str, dict[str, Any]] | None = None
_SCAN_CACHE_AT: float | None = None

__all__ = [
    "BleDeviceCollector",
    "scan_ble",
    "read_ble_services",
    "reset_scan_cache",
    "sanitize_services",
    "sanitize_heart_rate_wait",
    "STATUS_NOT_CONNECTED",
    "STATUS_NOT_FOUND",
    "MISSING_DEPENDENCY_MESSAGE",
]


def _load_bleak() -> Any:
    """Import bleak lazily; a missing install fails this collector only."""
    try:
        import bleak  # noqa: PLC0415 - deliberately lazy (optional dependency)
    except ImportError as exc:
        raise CollectorError(MISSING_DEPENDENCY_MESSAGE) from exc
    return bleak


def reset_scan_cache() -> None:
    """Forget the cached scan (used by tests and by long-lived callers)."""
    global _SCAN_CACHE, _SCAN_CACHE_AT
    with _SCAN_LOCK:
        _SCAN_CACHE = None
        _SCAN_CACHE_AT = None


def sanitize_services(value: Any) -> list[str]:
    """Clean a configured ``services`` list; all-invalid falls back to default."""
    if not isinstance(value, (list, tuple)):
        return list(DEFAULT_SERVICES)
    cleaned: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        name = item.strip().lower()
        if name in VALID_SERVICES and name not in cleaned:
            cleaned.append(name)
    return cleaned or list(DEFAULT_SERVICES)


def sanitize_heart_rate_wait(value: Any) -> float:
    """Parse ``heartRateWaitSeconds`` into 1..8 seconds (default 3)."""
    if value is None or isinstance(value, bool):
        return DEFAULT_HEART_RATE_WAIT_SECONDS
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_HEART_RATE_WAIT_SECONDS
    if seconds != seconds:  # NaN
        return DEFAULT_HEART_RATE_WAIT_SECONDS
    return max(MIN_HEART_RATE_WAIT_SECONDS, min(MAX_HEART_RATE_WAIT_SECONDS, seconds))


def _scan_entry_info(entry: Any, key: Any = None) -> dict[str, Any] | None:
    """Normalise one ``discover(return_adv=True)`` value into an info dict."""
    device: Any = entry
    advertisement: Any = None
    if isinstance(entry, (tuple, list)):
        if len(entry) >= 2:
            device, advertisement = entry[0], entry[1]
        elif len(entry) == 1:
            device = entry[0]

    address = getattr(device, "address", None)
    if not address and isinstance(key, str):
        address = key
    if not isinstance(address, str) or not address.strip():
        return None

    name = getattr(device, "name", None)
    local_name = getattr(advertisement, "local_name", None) if advertisement else None
    rssi = getattr(advertisement, "rssi", None) if advertisement else None
    return {
        "address": address.strip().upper(),
        "name": name if isinstance(name, str) and name else None,
        "local_name": (
            local_name if isinstance(local_name, str) and local_name else None
        ),
        "rssi": rssi if isinstance(rssi, int) and not isinstance(rssi, bool) else None,
    }


def _normalize_scan(discovered: Any) -> dict[str, dict[str, Any]]:
    """Turn a bleak discover result (dict or list) into address-keyed info dicts."""
    if isinstance(discovered, dict):
        items: Any = discovered.items()
    elif isinstance(discovered, (list, tuple)):
        items = ((None, entry) for entry in discovered)
    else:
        return {}

    scan: dict[str, dict[str, Any]] = {}
    for key, entry in items:
        info = _scan_entry_info(entry, key)
        if info is not None and info["address"] not in scan:
            scan[info["address"]] = info
    return scan


def scan_ble(timeout: float = SCAN_TIMEOUT_SECONDS) -> dict[str, dict[str, Any]]:
    """Scan once and return ``{UPPERCASE_ADDRESS: {address,name,local_name,rssi}}``.

    A failed scan (radio off, backend error) degrades to an empty mapping: the
    caller reports ``未发现`` rather than an error. A missing bleak install
    raises :class:`CollectorError` before any scanning is attempted.
    """
    bleak = _load_bleak()

    async def _discover() -> Any:
        return await bleak.BleakScanner.discover(timeout=timeout, return_adv=True)

    try:
        discovered = asyncio.run(_discover())
    except Exception:
        return {}
    return _normalize_scan(discovered)


def _get_scan() -> dict[str, dict[str, Any]]:
    """Return the shared scan result, refreshing it after ``SCAN_CACHE_TTL_SECONDS``."""
    global _SCAN_CACHE, _SCAN_CACHE_AT
    with _SCAN_LOCK:
        now = time.monotonic()
        if (
            _SCAN_CACHE is not None
            and _SCAN_CACHE_AT is not None
            and now - _SCAN_CACHE_AT < SCAN_CACHE_TTL_SECONDS
        ):
            return _SCAN_CACHE
        devices = scan_ble(SCAN_TIMEOUT_SECONDS)
        _SCAN_CACHE = devices if isinstance(devices, dict) else {}
        _SCAN_CACHE_AT = time.monotonic()
        return _SCAN_CACHE


def _parse_battery_level(payload: Any) -> int | None:
    """Decode the first byte of ``0x2A19``; only 0..100 is a valid percentage."""
    if payload is None:
        return None
    try:
        raw = bytes(payload)
    except (TypeError, ValueError):
        return None
    if not raw or not 0 <= raw[0] <= 100:
        return None
    return int(raw[0])


def _parse_heart_rate(payload: Any) -> int | None:
    """Decode ``0x2A37``: flags bit 0 selects uint8 vs little-endian uint16."""
    if payload is None:
        return None
    try:
        raw = bytes(payload)
    except (TypeError, ValueError):
        return None
    if len(raw) < 2:
        return None
    flags = raw[0]
    if flags & 0x01:
        if len(raw) < 3:
            return None
        return int.from_bytes(raw[1:3], "little")
    return int(raw[1])


async def _read_heart_rate(client: Any, wait_seconds: float) -> int | None:
    """Wait at most ``wait_seconds`` for the first heart-rate notification."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.001, float(wait_seconds))
    future: asyncio.Future[int | None] = loop.create_future()

    def _on_measurement(_characteristic: Any, data: Any) -> None:
        if future.done():
            return
        try:
            future.set_result(_parse_heart_rate(data))
        except Exception:
            future.set_result(None)

    try:
        await asyncio.wait_for(
            client.start_notify(
                HEART_RATE_MEASUREMENT_CHARACTERISTIC_UUID, _on_measurement
            ),
            timeout=max(0.001, deadline - loop.time()),
        )
        return await asyncio.wait_for(
            asyncio.shield(future), timeout=max(0.001, deadline - loop.time())
        )
    except Exception:
        return None
    finally:
        try:
            await asyncio.wait_for(
                client.stop_notify(HEART_RATE_MEASUREMENT_CHARACTERISTIC_UUID),
                timeout=1.0,
            )
        except Exception:
            pass


def _read_services_sync(
    bleak: Any, address: str, services: list[str], heart_rate_wait: float
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "connected": False,
        "battery": None,
        "heart_rate": None,
    }

    async def _run() -> None:
        client = bleak.BleakClient(address, timeout=CONNECT_TIMEOUT_SECONDS)
        try:
            await client.connect()
            if "battery" in services:
                try:
                    raw = await asyncio.wait_for(
                        client.read_gatt_char(BATTERY_LEVEL_CHARACTERISTIC_UUID),
                        timeout=READ_TIMEOUT_SECONDS,
                    )
                    result["battery"] = _parse_battery_level(raw)
                except Exception:
                    result["battery"] = None
            if "heart_rate" in services:
                result["heart_rate"] = await _read_heart_rate(
                    client, heart_rate_wait
                )
            result["connected"] = True
        finally:
            try:
                await asyncio.wait_for(
                    client.disconnect(), timeout=CONNECT_TIMEOUT_SECONDS
                )
            except Exception:
                pass

    asyncio.run(_run())
    return result


def read_ble_services(
    address: str, services: list[str], heart_rate_wait: float
) -> dict[str, Any]:
    """Connect and read the requested services; never raises.

    Returns ``{"connected": bool, "battery": int|None, "heart_rate": int|None}``.
    A connect failure, a failed GATT read or a missing dependency all surface as
    ``connected=False`` / ``None`` fields so the collector can report the
    ``已发现未连接`` state instead of failing the snapshot.
    """
    try:
        bleak = _load_bleak()
        return _read_services_sync(bleak, address, list(services), heart_rate_wait)
    except Exception:
        return {"connected": False, "battery": None, "heart_rate": None}


def _battery_reading(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 100 else None


def _heart_rate_reading(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 0 else None


class BleDeviceCollector(BaseCollector):
    """One explicitly configured BLE peripheral (``BleDevices`` entry)."""

    platform = "ble"

    def __init__(self, entry: dict[str, Any]) -> None:
        if not isinstance(entry, dict):
            raise CollectorError("BLE 配置项必须是对象")
        device_id = str(entry.get("id") or "").strip()
        if not device_id:
            raise CollectorError("BLE 配置缺少 id")
        self.device_id = device_id
        self.name = str(entry.get("name") or device_id).strip() or device_id

        address = str(entry.get("address") or "").strip()
        name_filter = str(entry.get("nameFilter") or "").strip()
        if not address and not name_filter:
            raise CollectorError("BLE 配置缺少 address 或 nameFilter")
        self.address = address.upper() or None
        self.name_filter = name_filter or None

        self.services = sanitize_services(entry.get("services"))
        self.heart_rate_wait = sanitize_heart_rate_wait(
            entry.get("heartRateWaitSeconds")
        )

    def _match_scan(self, scan: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        """Match by address first; otherwise by nameFilter against local_name/name."""
        if self.address:
            target = self.address.upper()
            for info in scan.values():
                if str(info.get("address") or "").upper() == target:
                    return info
            return None
        needle = (self.name_filter or "").lower()
        if not needle:
            return None
        for info in scan.values():
            local_name = info.get("local_name")
            candidate = local_name if local_name else info.get("name")
            if candidate and needle in str(candidate).lower():
                return info
        return None

    def get_data(self) -> dict[str, Any]:
        scan = _get_scan()
        found = self._match_scan(scan)
        last_seen = int(time.time()) if found is not None else None

        if found is None:
            return self._entry(
                address=self.address,
                name=None,
                rssi=None,
                connected=False,
                battery=None,
                heart_rate=None,
                status_text=STATUS_NOT_FOUND,
                online=False,
                degraded=False,
                last_seen=None,
                detail=self.address,
            )

        with _BLE_LOCK:
            reading = read_ble_services(
                found["address"], list(self.services), self.heart_rate_wait
            )
        reading = reading if isinstance(reading, dict) else {}
        connected = bool(reading.get("connected"))

        if connected:
            battery = _battery_reading(reading.get("battery"))
            heart_rate = _heart_rate_reading(reading.get("heart_rate"))
            degraded = (
                ("battery" in self.services and battery is None)
                or ("heart_rate" in self.services and heart_rate is None)
            )
            status_text = None
        else:
            battery = None
            heart_rate = None
            degraded = True
            status_text = STATUS_NOT_CONNECTED

        return self._entry(
            address=found["address"],
            name=found.get("local_name") or found.get("name") or self.name,
            rssi=found.get("rssi"),
            connected=connected,
            battery=battery,
            heart_rate=heart_rate,
            status_text=status_text,
            online=True,
            degraded=degraded,
            last_seen=last_seen,
            detail=found["address"],
        )

    def _entry(
        self,
        *,
        address: str | None,
        name: str | None,
        rssi: int | None,
        connected: bool,
        battery: int | None,
        heart_rate: int | None,
        status_text: str | None,
        online: bool,
        degraded: bool,
        last_seen: int | None,
        detail: str | None,
    ) -> dict[str, Any]:
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": self.name,
            "batteryLevel": battery,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "ble": {
                "address": address,
                "name": name,
                "rssi": rssi,
                "services": list(self.services),
                "connected": connected,
                "heartRate": heart_rate,
                "statusText": status_text,
                "lastSeenAt": last_seen,
            },
            "transport": make_transport(
                "ble",
                degraded=degraded,
                online=online,
                last_seen=last_seen,
                detail=detail,
            ),
            "error": None,
        }
