#!/usr/bin/env python3
r"""iPhone Wi-Fi collection probe — v3.4.0 research.

This is a standalone diagnostic script. It is NOT imported by status_core.py
and is not registered as a collector, so it cannot affect the stable USB path.

Two discovery paths are tested:

1. usbmuxd ``connection_type="Network"`` (classic iTunes AMDS supports this).
2. Bonjour/mobdev2 via ``get_mobdev2_lockdowns`` — this bypasses usbmuxd and
   works even with the Microsoft Store "Apple Devices" app, whose AMDS service
   does not discover Wi-Fi devices (pymobiledevice3#1968).

Usage:
    .\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py
    .\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --list
    .\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --discovery bonjour
    .\.venv\Scripts\python.exe .\collectors\iphone_wireless_test.py --serial <UDID>

Exit codes:
    0  connected and read successfully
    1  no Network device / connection failed
    2  pymobiledevice3 is unavailable
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import traceback

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


WIRELESS_HINT = (
    "请依次确认：\n"
    "  1. iPhone 已用 USB 连接过这台电脑，并在手机上点过「信任」；\n"
    "  2. 在 Finder（macOS）或 Apple Devices / iTunes（Windows）中\n"
    "     勾选「通过 Wi-Fi 同步」；\n"
    "  3. iPhone 和电脑连接同一个局域网，且 iPhone 已解锁；\n"
    "  4. 稍等几秒让电脑发现 Network 设备；\n"
    "  5. 必要时重新插拔 USB，再运行一次；\n"
    "  6. Windows 若安装的是 Microsoft Store 版 Apple Devices，其\n"
    "     Apple Mobile Device Service 不提供 Wi-Fi 发现；本脚本会自动\n"
    "     尝试 Bonjour/mobdev2，仍失败可改装经典版 iTunes；\n"
    "  7. 如果设备信息能读到但电量服务被断开，通常是 iPhone 锁屏/休眠，\n"
    "     请保持解锁并接上电源再试。"
)


def _print_hint() -> None:
    print("\n提示：")
    print(WIRELESS_HINT)


async def _list_usbmux_devices():
    from pymobiledevice3.usbmux import list_devices

    return await list_devices()


async def _iter_bonjour_lockdowns(
    serial: str | None, timeout: float
):
    from pymobiledevice3.lockdown import get_mobdev2_lockdowns

    async for ip, lockdown in get_mobdev2_lockdowns(
        udid=serial, only_paired=True, timeout=timeout
    ):
        yield ip, lockdown


def _print_usbmux_devices(devices) -> None:
    if not devices:
        print("  （usbmuxd 没有发现设备）")
        return
    for device in devices:
        print(f"  - {device.serial}  [{device.connection_type}]")


async def _print_bonjour_devices(timeout: float) -> int:
    from pymobiledevice3.lockdown import get_mobdev2_lockdowns

    count = 0
    try:
        async for ip, lockdown in get_mobdev2_lockdowns(
            only_paired=True, timeout=timeout
        ):
            values = lockdown.all_values or {}
            count += 1
            print(
                f"  - {getattr(lockdown, 'udid', '未知')}  "
                f"[Bonjour {ip}]  {values.get('ProductType', '未知')}"
            )
            await lockdown.close()
    except Exception as exc:
        print(f"  （Bonjour 发现失败：{type(exc).__name__}: {exc}）")
    return count


def _print_device(client, source: str, ip: str | None = None) -> None:
    from pymobiledevice3.services.diagnostics import DiagnosticsService

    values = client.all_values or {}
    product_type = values.get("ProductType") or "未知"
    device_name = values.get("DeviceName") or "未知"
    product_version = values.get("ProductVersion") or "未知"
    build_version = values.get("BuildVersion") or "未知"
    udid = getattr(client, "udid", None) or "未知"

    print(f"[OK] iPhone Wi-Fi 连接成功（{source}）")
    print(f"  设备名称 : {device_name}")
    print(f"  型号      : {product_type}")
    print(f"  系统版本  : {product_version} ({build_version})")
    print(f"  UDID      : {udid}" + (f"  IP: {ip}" if ip else ""))
    print(f"  数据通道  : {'Bonjour/mobdev2 TCP' if ip else 'usbmuxd Network'}")


async def _read_battery(client, retries: int = 2) -> bool:
    from pymobiledevice3.services.diagnostics import DiagnosticsService

    for attempt in range(1, retries + 1):
        try:
            async with DiagnosticsService(lockdown=client) as diagnostics:
                battery = await diagnostics.get_battery()
            level = battery.get(
                "BatteryCurrentCapacity", battery.get("CurrentCapacity")
            )
            charging = battery.get("BatteryIsCharging", battery.get("IsCharging"))
            print(f"  电量      : {'未知' if level is None else f'{level}%'}")
            print(f"  充电状态  : {charging}")
            return True
        except Exception as exc:
            if attempt >= retries:
                print(
                    f"  [WARN] 连接成功，但电量服务被设备断开："
                    f"{type(exc).__name__}: {exc}"
                )
                print("        这通常是 iPhone 锁屏/休眠导致，请保持解锁并接电源后重试。")
                return False
            await asyncio.sleep(1)
    return False


async def _via_usbmux(serial: str | None, timeout: float) -> bool:
    from pymobiledevice3.lockdown import create_using_usbmux

    try:
        devices = await _list_usbmux_devices()
    except ModuleNotFoundError:
        raise
    except Exception as exc:
        print(f"[INFO] usbmuxd 不可用：{type(exc).__name__}: {exc}")
        return False

    network_devices = [d for d in devices if d.connection_type == "Network"]
    if not network_devices:
        usb = [d for d in devices if d.connection_type == "USB"]
        print("[INFO] usbmuxd 未列出 Network 设备。")
        if usb:
            print("[INFO] 检测到 USB 设备；插着数据线时 Network 可能被隐藏，可拔线几秒后重试。")
        return False

    target = None
    if serial:
        for device in network_devices:
            if device.serial == serial or device.matches_udid(serial):
                target = device
                break
        if target is None:
            print(f"[INFO] 指定的设备不在 Network 列表中：{serial}")
            return False
    else:
        target = network_devices[0]

    try:
        client = await asyncio.wait_for(
            create_using_usbmux(
                serial=target.serial,
                connection_type="Network",
                autopair=False,
            ),
            timeout=timeout,
        )
        async with client:
            _print_device(client, "usbmuxd Network")
            await _read_battery(client)
        return True
    except asyncio.TimeoutError:
        print(f"[INFO] usbmuxd Network 连接超时（{timeout:g}s）。")
        return False
    except Exception as exc:
        print(f"[INFO] usbmuxd Network 连接失败：{type(exc).__name__}: {exc}")
        return False


async def _via_bonjour(serial: str | None, timeout: float) -> bool:
    try:
        async for ip, lockdown in _iter_bonjour_lockdowns(serial, timeout):
            try:
                _print_device(lockdown, "Bonjour/mobdev2", ip)
                await _read_battery(lockdown)
            finally:
                await lockdown.close()
            return True
        print("[INFO] Bonjour/mobdev2 未发现已配对的 iPhone。")
        return False
    except ModuleNotFoundError:
        raise
    except Exception as exc:
        print(f"[INFO] Bonjour/mobdev2 发现失败：{type(exc).__name__}: {exc}")
        return False


async def _list_all(args: argparse.Namespace) -> int:
    found = 0
    print("=== usbmuxd ===")
    try:
        devices = await _list_usbmux_devices()
        _print_usbmux_devices(devices)
        found += len([d for d in devices if d.connection_type == "Network"])
    except ModuleNotFoundError:
        raise
    except Exception as exc:
        print(f"  （usbmuxd 不可用：{type(exc).__name__}: {exc}）")

    print("=== Bonjour/mobdev2 ===")
    found += await _print_bonjour_devices(args.bonjour_timeout)
    return 0 if found else 1


async def _main_async(args: argparse.Namespace) -> int:
    try:
        if args.list:
            return await _list_all(args)

        if args.discovery == "usbmux":
            return 0 if await _via_usbmux(args.serial, args.timeout) else 1
        if args.discovery == "bonjour":
            return 0 if await _via_bonjour(args.serial, args.bonjour_timeout) else 1

        # auto: try usbmuxd Network first, then Bonjour/mobdev2.
        if await _via_usbmux(args.serial, args.timeout):
            return 0
        if await _via_bonjour(args.serial, args.bonjour_timeout):
            return 0

        print("[FAIL] 没有找到可用的 iPhone Wi-Fi 连接。")
        _print_hint()
        return 1
    except ModuleNotFoundError as exc:
        print(f"[FAIL] 缺少依赖：{exc}")
        print("请先运行：.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return 2
    except Exception as exc:
        print(f"[FAIL] 预研脚本异常：{type(exc).__name__}: {exc}")
        _print_hint()
        if args.verbose:
            traceback.print_exc()
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe iPhone Wi-Fi connection (usbmuxd Network + Bonjour/mobdev2)."
    )
    parser.add_argument(
        "--serial",
        default=None,
        help="target device serial/UDID (default: first discovered device)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="usbmuxd connection timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "--bonjour-timeout",
        type=float,
        default=6.0,
        help="Bonjour browse timeout in seconds (default: 6)",
    )
    parser.add_argument(
        "--discovery",
        choices=("auto", "usbmux", "bonjour"),
        default="auto",
        help="discovery path (default: auto)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list usbmuxd and Bonjour/mobdev2 devices and exit",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print traceback on failure",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
