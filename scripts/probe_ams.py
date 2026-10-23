#!/usr/bin/env python3
"""Standalone Apple Media Service (AMS) probe for StatusCast.

This is a diagnostic tool, not the final MediaSource. It:

1. scans for Apple BLE devices,
2. connects to the first one that exposes the AMS service,
3. subscribes to Entity Update notifications,
4. registers Player / Track attributes,
5. prints raw and decoded media changes for a while.

Run it on the target Windows machine while the iPhone is unlocked and music is
playing. Use --address to target a specific device, and --pair if Windows has
not bonded with it yet.

Exit codes:
    0  probe completed
    2  no AMS-capable device / target not found
    3  authentication or pairing required
    4  other BLE failure
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Any

# Allow ``python scripts/probe_ams.py`` from the project root without installing
# the package. The real implementation lives in core.sources.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.models import PlaybackState  # noqa: E402
from core.sources.ams_protocol import (  # noqa: E402
    AMS_SERVICE,
    AMS_UPDATE,
    PLAYBACK_STATE_NAMES,
    PLAYER_REGISTRATION,
    TRACK_REGISTRATION,
    AmsMediaState,
    parse_entity_update,
)

EXIT_OK = 0
EXIT_NOT_FOUND = 2
EXIT_AUTH = 3
EXIT_ERROR = 4


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def detail_lines(state: AmsMediaState) -> list[str]:
    playback = (
        PLAYBACK_STATE_NAMES.get(state.playback_state, str(state.playback_state))
        if state.playback_state is not None
        else "unknown"
    )
    lines = [
        f"  player : {state.player_name or '-'}",
        f"  state  : {playback} (rate={state.playback_rate:g})",
        f"  track  : {state.title or '-'}",
        f"  artist : {state.artist or '-'}",
        f"  album  : {state.album or '-'}",
        (
            f"  timing : elapsed={format_elapsed(state.elapsed)}"
            f" / duration={format_elapsed(state.duration) if state.duration is not None else '-'}"
        ),
    ]
    if state.volume is not None:
        lines.append(f"  volume : {state.volume:.2f}")
    return lines


def status_line(state: AmsMediaState) -> str:
    status = state.to_status()
    if not status.title:
        if status.playback_state is not None:
            name = PLAYBACK_STATE_NAMES.get(
                state.playback_state, str(state.playback_state)
            )
            return f"🎵 未在播放（{name}）"
        return "🎵 尚未收到媒体状态"
    icon = "⏸️" if status.playback_state == PlaybackState.PAUSED else "🎵"
    artist = f" - {status.artist}" if status.artist else ""
    return f"{icon} {status.title}{artist}"


async def find_target(
    address: str | None,
    scan_seconds: float,
    connect_timeout: float,
) -> tuple[Any, Any, Any] | None:
    """Return (BLEDevice, advertisement, model) for an AMS-capable Apple device."""
    from bleak import BleakClient, BleakScanner

    print(f"扫描 BLE 设备（{scan_seconds:g} 秒）...")
    discovered = await BleakScanner.discover(
        timeout=scan_seconds,
        return_adv=True,
    )

    apple_devices: list[tuple[Any, Any]] = []
    for dev, adv in discovered.values():
        if 0x004C in (adv.manufacturer_data or {}):
            apple_devices.append((dev, adv))
            print(
                f"  发现 Apple 设备 {dev.address} "
                f"rssi={adv.rssi} name={dev.name!r}"
            )

    if address:
        wanted = address.upper()
        apple_devices = [
            (dev, adv)
            for dev, adv in apple_devices
            if dev.address.upper() == wanted
        ]
        if not apple_devices:
            print(f"没有在扫描结果中找到指定地址 {address}", file=sys.stderr)
            print(
                "请确认 iPhone 已解锁、蓝牙开启、靠近电脑，然后重试。",
                file=sys.stderr,
            )
            return None

    for dev, adv in apple_devices:
        print(f"\n尝试连接 {dev.address} ...")
        client = BleakClient(dev, timeout=connect_timeout, pair=False)
        try:
            await client.connect()
            ams = client.services.get_service(AMS_SERVICE)
            if ams is None:
                print("  未暴露 AMS，跳过。")
                continue

            model = "?"
            try:
                model = bytes(await client.read_gatt_char("2a24")).decode(
                    "utf-8", errors="replace"
                )
            except Exception:
                pass
            print(f"  找到 AMS：model={model}")
            return dev, adv, model
        except Exception as exc:
            print(f"  连接失败：{type(exc).__name__}: {exc}")
        finally:
            if client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    return None


async def run_probe(
    address: str | None,
    scan_seconds: float,
    listen_seconds: float,
    pair: bool,
    debug_raw: bool,
) -> int:
    from bleak import BleakClient
    from bleak.exc import BleakError, BleakGATTProtocolError

    target = await find_target(address, scan_seconds, connect_timeout=12.0)
    if target is None:
        print("\n没有找到可用的 AMS 设备。", file=sys.stderr)
        print(
            "建议：解锁 iPhone，打开音乐 App 并播放，确认蓝牙已开启，再重试。",
            file=sys.stderr,
        )
        return EXIT_NOT_FOUND

    dev, adv, model = target
    print(f"\n连接 {dev.address} (model={model})，pair={pair} ...")
    client = BleakClient(dev, timeout=25, pair=pair)
    state = AmsMediaState()
    notification_count = 0

    def on_update(_char: Any, data: bytearray) -> None:
        nonlocal notification_count
        notification_count += 1
        raw = bytes(data)
        try:
            note = parse_entity_update(raw)
        except ValueError as exc:
            print(f"[RAW?] {raw.hex()} ({exc})")
            return
        state.apply(note)
        suffix = " [truncated]" if note.truncated else ""
        if debug_raw:
            print(
                f"[RAW] {note.name} flags=0x{note.flags:02x} "
                f"value={note.value!r}{suffix} bytes={raw.hex()}"
            )
        else:
            print(f"[{time.strftime('%H:%M:%S')}] {note.name} = {note.value!r}{suffix}")

    try:
        await client.connect()
        if not client.is_connected:
            print("连接失败：client.is_connected=False", file=sys.stderr)
            return EXIT_ERROR

        if client.services.get_service(AMS_SERVICE) is None:
            print("连接成功但 AMS 不存在。", file=sys.stderr)
            return EXIT_NOT_FOUND

        try:
            await client.start_notify(AMS_UPDATE, on_update)
        except BleakGATTProtocolError as exc:
            if "Insufficient Authentication" in str(exc):
                print(
                    "\nAMS 要求已认证的 BLE 连接（Insufficient Authentication）。",
                    file=sys.stderr,
                )
                print(
                    "请用 --pair 重试；如果仍失败，先在 Windows 设置 → 蓝牙和其他设备中"
                    "完成配对，并在 iPhone 上确认。",
                    file=sys.stderr,
                )
                return EXIT_AUTH
            raise

        print("Entity Update 通知已订阅，注册 Player/Track 属性...")
        await client.write_gatt_char(AMS_UPDATE, PLAYER_REGISTRATION, response=True)
        await client.write_gatt_char(AMS_UPDATE, TRACK_REGISTRATION, response=True)

        print(f"监听 {listen_seconds:g} 秒。请在 iPhone 上播放/暂停/切歌...\n")
        deadline = time.monotonic() + listen_seconds
        last_printed = ""
        while time.monotonic() < deadline:
            await asyncio.sleep(1.0)
            line = status_line(state)
            if line != last_printed:
                print(f"\n状态：{line}")
                for detail in detail_lines(state):
                    print(detail)
                print()
                last_printed = line

        print(f"\n完成：收到 {notification_count} 条通知。")
        print(f"最终状态：{status_line(state)}")
        return EXIT_OK
    except BleakError as exc:
        text = str(exc)
        if "pair" in text.lower() or "authentication" in text.lower():
            print(f"\n配对/认证失败：{exc}", file=sys.stderr)
            print(
                "请确认 iPhone 已解锁并点击配对确认；必要时删除 Windows 中的旧配对记录后重试。",
                file=sys.stderr,
            )
            return EXIT_AUTH
        print(f"BLE 错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:
        print(f"probe 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        try:
            if client.is_connected:
                await client.stop_notify(AMS_UPDATE)
        except Exception:
            pass
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="扫描并订阅 iPhone 的 Apple Media Service（AMS）通知。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--address", help="指定 BLE 地址；默认自动寻找暴露 AMS 的 Apple 设备")
    parser.add_argument("--scan-seconds", type=float, default=8.0, help="BLE 扫描时长")
    parser.add_argument("--listen", type=float, default=20.0, help="订阅后监听时长")
    parser.add_argument(
        "--pair",
        action="store_true",
        help="连接时尝试配对（首次使用 AMS 通常需要）",
    )
    parser.add_argument("--debug-raw", action="store_true", help="打印原始通知字节")
    args = parser.parse_args(argv)

    try:
        return asyncio.run(
            run_probe(
                address=args.address,
                scan_seconds=args.scan_seconds,
                listen_seconds=args.listen,
                pair=args.pair,
                debug_raw=args.debug_raw,
            )
        )
    except KeyboardInterrupt:
        print("\n用户中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
