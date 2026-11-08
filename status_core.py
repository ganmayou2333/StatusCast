#!/usr/bin/env python3
"""StatusCast Python dispatcher.

Runs every registered collector in parallel, isolates failures per device,
and prints one standard JSON snapshot (the same shape the daemon writes to
run/status.json):

    {
      "schema": 3,
      "generatedAt": 1792860000,
      "devices": [
        {"id": "iphone", "platform": "ios", ..., "error": null},
        {"id": "android", "platform": "android", ..., "error": null}
      ]
    }

A disconnected device only sets ``error`` on its own entry; it never prevents
the other collector from returning data.

Beyond the always-on phone collectors, ``config.json`` may declare extra
devices: ``Consoles`` entries are probed over the LAN (C1, ARP/ping),
``SshNodes`` entries are probed through the Windows OpenSSH client (C2b,
Mac/Linux nodes) and ``BleDevices`` entries are scanned/connected as Bluetooth
LE peripherals. A local ``NekoTrak`` installation is collected as its own
``windows`` device; a machine that never ran NekoTrak keeps today's output
exactly. Missing files, broken JSON or empty lists keep the v3.3
behaviour exactly. Collectors run in a thread pool so one node's worst-case
8 s SSH timeout never stacks with the next one's; at most eight run at once
and larger configs are processed in ordered batches of eight.

The whole round also has a budget (``ROUND_BUDGET_SECONDS``, below the
daemon's 30 s process kill). Once it is spent no further collector starts and
every device that never got its turn reports one ``采集预算用尽（本轮未完成）``
error row, so a single slow path degrades its own device instead of starving
the round and turning every row stale.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collectors.android_collector import AndroidCollector
from collectors.ble_collector import BleDeviceCollector
from collectors.console_collector import ConsoleCollector
from collectors.iphone_collector import IPhoneCollector
from collectors.nekotrak_collector import NekoTrakCollector
from collectors.nekotrak_collector import available as nekotrak_available
from collectors.ssh_node_collector import SshNodeCollector
from collectors.base import CollectorError, error_entry
from collectors.lan_probe import make_transport

ROOT = Path(__file__).resolve().parent

# Upper bound on concurrently probed devices; bigger configs run in batches.
MAX_PARALLEL_COLLECTORS = 8

# Wall-clock budget for one round, deliberately below the daemon's 30 s process
# kill: a round that cannot finish reports its finished devices plus one error
# row per device that never started, instead of being killed as a whole.
ROUND_BUDGET_SECONDS = 25

# ``error`` text for a device the round budget no longer covers.
BUDGET_EXHAUSTED_MESSAGE = "采集预算用尽（本轮未完成）"


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Read config.json next to this script (or an explicit path).

    Missing file, unreadable file, bad JSON or a non-object root all degrade
    to an empty config instead of failing the whole snapshot.
    """
    path = Path(config_path) if config_path else ROOT / "config.json"
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _invalid_entry(platform: str, device_id: str, message: str) -> dict[str, Any]:
    """Device entry for a config row so broken it cannot build a collector.

    Delegates to :func:`collectors.base.error_entry` so the dispatcher's invalid
    rows and the collector error rows can never drift apart.
    """
    return error_entry(platform, device_id, message or "配置无效")


def _phone_entry(config: dict[str, Any], section: str) -> dict[str, Any] | None:
    """Return the optional phone config section as a dict (else None)."""
    entry = config.get(section)
    return entry if isinstance(entry, dict) else None


def _console_ids(config: dict[str, Any]) -> set[str]:
    """Console ids this config declares, i.e. the live set for state pruning.

    The console state file is pruned to exactly these ids on every write, so a
    host removed from ``config.json`` stops keeping a ``failStreak`` record
    forever. Rows too broken to build a collector still count as declared: they
    may be fixed on the next round and their hysteresis should survive that.
    """
    consoles = config.get("Consoles")
    if not isinstance(consoles, list):
        return set()
    return {
        str(entry.get("id") or "device").strip() or "device"
        for entry in consoles
        if isinstance(entry, dict)
    }


def build_collectors(
    config: dict[str, Any],
    adb: str | None = None,
    serial: str | None = None,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Return (collectors, invalid-entry devices) from a loaded config."""
    collectors: list[Any] = []
    invalid: list[dict[str, Any]] = []

    # Phone collectors are always present; a bad ``IPhone`` / ``Android``
    # section becomes one invalid entry instead of failing the whole refresh.
    for section, device_id, platform, factory in (
        ("IPhone", "iphone", "ios", IPhoneCollector),
        ("Android", "android", "android", AndroidCollector),
    ):
        try:
            if section == "Android":
                collector = factory(
                    entry=_phone_entry(config, section), adb=adb, serial=serial
                )
            else:
                collector = factory(entry=_phone_entry(config, section))
            collectors.append(collector)
        except CollectorError as exc:
            invalid.append(_invalid_entry(platform, device_id, str(exc)))

    console_ids = _console_ids(config)
    for section, factory in (
        ("Consoles", ConsoleCollector),
        ("SshNodes", SshNodeCollector),
    ):
        platform = "console" if section == "Consoles" else "linux"
        # Only consoles share the persisted hysteresis file, so only they need
        # to know which ids the config still declares.
        extra = {"live_ids": console_ids} if section == "Consoles" else {}
        for entry in config.get(section) or []:
            if not isinstance(entry, dict):
                invalid.append(_invalid_entry(platform, str(section), "配置项必须是对象"))
                continue
            device_id = str(entry.get("id") or "device").strip() or "device"
            try:
                collectors.append(factory(entry, **extra))
            except CollectorError as exc:
                invalid.append(
                    _invalid_entry(
                        str(entry.get("platform") or platform), device_id, str(exc)
                    )
                )

    # BLE peripherals are opt-in: missing / non-list sections are ignored and
    # every entry keeps its own id so one broken row cannot hide the others.
    ble_entries = config.get("BleDevices")
    if isinstance(ble_entries, list):
        for entry in ble_entries:
            if not isinstance(entry, dict):
                invalid.append(_invalid_entry("ble", "ble", "配置项必须是对象"))
                continue
            device_id = str(entry.get("id") or "").strip() or "ble"
            try:
                collectors.append(BleDeviceCollector(entry))
            except CollectorError as exc:
                invalid.append(_invalid_entry("ble", device_id, str(exc)))

    # NekoTrak runs on this PC instead of being config-driven. It is only added
    # when its data root exists, so a machine that never ran NekoTrak gets no
    # permanently-red row -- the same "absent section, absent device" rule the
    # config-driven collectors follow. Whether it is *running* is decided per
    # refresh, so a killed app turns into the standard error entry.
    if nekotrak_available():
        try:
            collectors.append(NekoTrakCollector())
        except CollectorError as exc:
            invalid.append(_invalid_entry("windows", "nekotrak", str(exc)))

    return collectors, invalid


def run_collector(collector: Any) -> dict[str, Any]:
    try:
        return collector.get_data()
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        payload = collector.error_payload(message)
        # A device whose every channel failed still exposes a transport block
        # (no active channel) so consumers see one stable shape per entry.
        payload.setdefault(
            "transport",
            make_transport("none", degraded=False, online=False, detail=None),
        )
        return payload


def _budget_spent(deadline: float | None) -> bool:
    """True once the round deadline has passed (``None`` means no deadline)."""
    return deadline is not None and time.monotonic() >= deadline


def _skipped_entry(collector: Any) -> dict[str, Any]:
    """One attributable error row for a collector the budget did not reach."""
    return _invalid_entry(
        collector.platform, collector.device_id, BUDGET_EXHAUSTED_MESSAGE
    )


def _run_or_skip(collector: Any, deadline: float | None = None) -> dict[str, Any]:
    """Run one collector, or skip it when the round budget is already spent.

    The check sits immediately before the collector starts: a collector that
    already started is always allowed to finish (its own per-call timeouts bound
    it, and a running thread cannot be cancelled), so only devices that never
    got their turn are reported as budget-exhausted.
    """
    if _budget_spent(deadline):
        return _skipped_entry(collector)
    return run_collector(collector)


def _run_collectors(
    collectors: list[Any],
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Run collectors in ordered batches of at most eight, parallel per batch.

    ``deadline`` is a ``time.monotonic()`` value (``None`` means no budget). It
    is checked between batches and again right before each collector starts;
    whatever the budget no longer covers becomes one attributable error row, so
    the results that did finish are still returned in their original order.
    """
    devices: list[dict[str, Any]] = []
    for start in range(0, len(collectors), MAX_PARALLEL_COLLECTORS):
        batch = collectors[start : start + MAX_PARALLEL_COLLECTORS]
        if _budget_spent(deadline):
            # Do not even open a pool for a batch this round cannot finish.
            devices.extend(_skipped_entry(collector) for collector in batch)
            continue
        with ThreadPoolExecutor(
            max_workers=min(len(batch), MAX_PARALLEL_COLLECTORS)
        ) as pool:
            devices.extend(pool.map(partial(_run_or_skip, deadline=deadline), batch))
    return devices


def collect_all(
    adb: str | None = None,
    serial: str | None = None,
    config: dict[str, Any] | None = None,
    budget_seconds: float | None = None,
) -> dict[str, Any]:
    """Run all collectors in parallel and merge the results into one snapshot.

    ``budget_seconds`` overrides ``ROUND_BUDGET_SECONDS`` for this round (tests
    inject a tiny budget); ``None`` keeps the module default. The budget only
    decides whether a collector *starts*, never what a finished one returns.
    """
    deadline = time.monotonic() + (
        ROUND_BUDGET_SECONDS if budget_seconds is None else float(budget_seconds)
    )
    cfg = load_config() if config is None else config
    collectors, invalid = build_collectors(cfg, adb=adb, serial=serial)

    devices = (
        _run_collectors(collectors, deadline=deadline) if collectors else []
    )

    return {
        "schema": 3,
        "generatedAt": int(time.time()),
        "devices": devices + invalid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect device status (phones + configured LAN devices) and print one JSON snapshot."
    )
    parser.add_argument(
        "--adb",
        default=None,
        help="adb executable (default: ADB env var or 'adb')",
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="Android serial (default: ANDROID_SERIAL env var)",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="alternative config.json path (default: next to status_core.py)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output (default: compact one-line JSON).",
    )
    args = parser.parse_args()

    config = load_config(args.config) if args.config else None
    payload = collect_all(adb=args.adb, serial=args.serial, config=config)
    if args.pretty:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
