"""Offline tests for the Android fallback transport chain (no real adb)."""

from __future__ import annotations

import json
import time
import unittest
from unittest import mock

from collectors import android_collector as ad
from collectors.base import BUDGET_EXHAUSTED_MESSAGE
from collectors.base import CollectorError

EMPTY_DEVICES = "List of devices attached\n\n"
USB_DEVICES = "List of devices attached\nUSB123\tdevice\n"
TCP_DEVICES = "List of devices attached\n192.168.1.21:5555\tdevice\n"
MDNS_DEVICES = "List of devices attached\n192.168.1.22:5555\tdevice\n"

BATTERY = "  level: 42\n  status: 2\n"

SHELL_OUTPUTS = {
    ("shell", "getprop", "ro.product.model"): "Pixel 7",
    ("shell", "getprop", "ro.product.manufacturer"): "Google",
    ("shell", "dumpsys", "battery"): BATTERY,
    ("shell", "dumpsys", "activity", "activities"): "",
    ("shell", "dumpsys", "window", "displays"): "",
    ("shell", "dumpsys", "media_session"): "",
}

MDNS_OUTPUT = """List of discovered mdns services
random noise from adb
adb-ABC123\t_adb-tls-connect._tcp\t192.168.1.21:37000
adb-XYZ\t_adb-tls-pairing._tcp\t192.168.1.21:37001
adb-DEF456\t_adb-tls-connect._tcp\t192.168.1.22:5555
"""


class FakeAdb:
    """Deterministic stand-in for ``_run_adb``."""

    def __init__(
        self,
        device_sequences,
        mdns_output: str = "",
        connect_outputs=None,
        pair_output: str = "Successfully paired to 192.0.2.10:37000\n",
    ):
        self.device_sequences = list(device_sequences)
        self.mdns_output = mdns_output
        self.connect_outputs = list(connect_outputs or [])
        self.pair_output = pair_output
        self.calls: list[tuple[list[str], bool]] = []

    def __call__(self, args, timeout=8, serial=True):
        self.calls.append((list(args), serial))
        key = tuple(args)
        if key == ("devices",):
            if self.device_sequences:
                return self.device_sequences.pop(0)
            return EMPTY_DEVICES
        if key == ("mdns", "services"):
            return self.mdns_output
        if args[:1] == ["connect"]:
            return self.connect_outputs.pop(0) if self.connect_outputs else "connected to x\n"
        if args[:1] == ["pair"]:
            return self.pair_output
        return SHELL_OUTPUTS.get(key, "")

    def adb_args(self):
        return [args for args, _serial in self.calls]


def build(entry=None, fake=None):
    collector = ad.AndroidCollector(entry=entry)
    patcher = mock.patch.object(collector, "_run_adb", side_effect=fake)
    patcher.start()
    return collector, patcher


class MdnsParsingTests(unittest.TestCase):
    def test_extracts_connect_rows_only(self):
        self.assertEqual(
            ad.parse_mdns_services(MDNS_OUTPUT),
            ["192.168.1.21:37000", "192.168.1.22:5555"],
        )

    def test_noise_only_output_is_empty(self):
        self.assertEqual(ad.parse_mdns_services("nothing here\n"), [])
        self.assertEqual(ad.parse_mdns_services(None), [])

    def test_last_host_port_token_wins(self):
        output = "adb-X\t_adb-tls-connect._tcp\t10.0.0.5:5555\tother:1\n"
        self.assertEqual(ad.parse_mdns_services(output), ["other:1"])


class ConstructorTests(unittest.TestCase):
    def test_defaults(self):
        collector = ad.AndroidCollector()
        self.assertIsNone(collector.ip)
        self.assertEqual(collector.port, 5555)
        self.assertEqual(collector.pair_port, 0)
        self.assertIsNone(collector.pair_code)
        self.assertTrue(collector.fallback)

    def test_entry_options(self):
        collector = ad.AndroidCollector(
            {
                "ip": "192.0.2.10",
                "port": 5556,
                "pairPort": 37001,
                "pairCode": "123456",
                "fallback": False,
            }
        )
        self.assertEqual(collector.ip, "192.0.2.10")
        self.assertEqual(collector.port, 5556)
        self.assertEqual(collector.pair_port, 37001)
        self.assertEqual(collector.pair_code, "123456")
        self.assertFalse(collector.fallback)

    def test_invalid_ip_raises(self):
        with self.assertRaises(CollectorError) as ctx:
            ad.AndroidCollector({"ip": "nope"})
        self.assertIn("IP 非法", str(ctx.exception))

    def test_invalid_ports_fall_back(self):
        collector = ad.AndroidCollector(
            {"port": 0, "pairPort": "bad"}
        )
        self.assertEqual(collector.port, 5555)
        self.assertEqual(collector.pair_port, 0)


class PrimaryChannelTests(unittest.TestCase):
    def test_usb_device_is_collected_with_adb_transport(self):
        fake = FakeAdb([USB_DEVICES])
        collector, patcher = build(fake=fake)
        self.addCleanup(patcher.stop)
        entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["batteryLevel"], 42)
        self.assertIs(entry["isCharging"], True)
        self.assertEqual(entry["model"], "Google Pixel 7")
        self.assertEqual(
            entry["transport"],
            {
                "active": "adb",
                "degraded": False,
                "online": True,
                "lastSeenAt": entry["transport"]["lastSeenAt"],
                "detail": "adb:USB123",
            },
        )
        self.assertNotIn(["mdns", "services"], fake.adb_args())

    def test_tcp_serial_uses_adb_wifi_transport(self):
        fake = FakeAdb([TCP_DEVICES])
        collector, patcher = build(fake=fake)
        self.addCleanup(patcher.stop)
        entry = collector.get_data()
        self.assertEqual(entry["transport"]["active"], "adb-wifi")
        self.assertEqual(entry["transport"]["detail"], "adb:192.168.1.21:5555")


class FallbackChainTests(unittest.TestCase):
    def test_mdns_connect_then_collect(self):
        fake = FakeAdb(
            [EMPTY_DEVICES, MDNS_DEVICES],
            mdns_output=MDNS_OUTPUT,
            connect_outputs=[
                "failed to connect to '192.168.1.21:37000'\n",
                "connected to 192.168.1.22:5555\n",
            ],
        )
        collector, patcher = build(fake=fake)
        self.addCleanup(patcher.stop)
        entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["transport"]["active"], "adb-wifi")
        self.assertEqual(entry["transport"]["detail"], "adb:192.168.1.22:5555")
        calls = fake.calls
        connect_calls = [(args, serial) for args, serial in calls
                         if args[:1] == ["connect"]]
        self.assertEqual(connect_calls[0], (["connect", "192.168.1.21:37000"], False))
        self.assertEqual(connect_calls[1], (["connect", "192.168.1.22:5555"], False))
        mdns_call = next(c for c in calls if c[0] == ["mdns", "services"])
        self.assertFalse(mdns_call[1])
        self.assertFalse(any(args[:1] == ["pair"] for args, _ in calls))

    def test_pair_runs_before_ip_connect_and_never_leaks_code(self):
        fake = FakeAdb(
            [EMPTY_DEVICES, EMPTY_DEVICES],
            connect_outputs=["failed to connect to '192.0.2.10:5555'\n"],
        )
        collector, patcher = build(
            entry={
                "ip": "192.0.2.10",
                "pairPort": 37000,
                "pairCode": "SECRET-CODE-123",
                "fallback": False,
            },
            fake=fake,
        )
        self.addCleanup(patcher.stop)
        with self.assertRaises(CollectorError) as ctx:
            collector.get_data()
        self.assertNotIn("SECRET-CODE-123", str(ctx.exception))
        args_list = fake.adb_args()
        pair_index = args_list.index(
            ["pair", "192.0.2.10:37000", "SECRET-CODE-123"]
        )
        connect_index = args_list.index(["connect", "192.0.2.10:5555"])
        self.assertLess(pair_index, connect_index)

    def test_pair_code_absent_from_degraded_snapshot(self):
        fake = FakeAdb([EMPTY_DEVICES])
        collector, patcher = build(
            entry={
                "ip": "192.0.2.10",
                "pairPort": 37000,
                "pairCode": "SECRET-CODE-123",
            },
            fake=fake,
        )
        self.addCleanup(patcher.stop)
        with mock.patch.object(
            ad, "probe_lan", return_value=(False, "lan", None)
        ):
            entry = collector.get_data()
        dumped = json.dumps(entry, ensure_ascii=False)
        self.assertNotIn("SECRET-CODE-123", dumped)
        self.assertNotIn("pairCode", dumped)
        self.assertIsNone(entry["error"])

    def test_ip_connect_success_then_collect(self):
        fake = FakeAdb(
            [EMPTY_DEVICES, TCP_DEVICES],
            connect_outputs=["connected to 192.168.1.21:5555\n"],
        )
        collector, patcher = build(entry={"ip": "192.168.1.21"}, fake=fake)
        self.addCleanup(patcher.stop)
        entry = collector.get_data()
        self.assertEqual(entry["transport"]["active"], "adb-wifi")
        self.assertEqual(entry["transport"]["detail"], "adb:192.168.1.21:5555")

    def test_failure_with_ip_reports_degraded_online(self):
        fake = FakeAdb(
            [EMPTY_DEVICES, EMPTY_DEVICES],
            connect_outputs=["failed to connect\n"],
        )
        collector, patcher = build(entry={"ip": "192.0.2.10"}, fake=fake)
        self.addCleanup(patcher.stop)
        with mock.patch.object(
            ad, "probe_lan", return_value=(True, "arp", None)
        ) as probe:
            entry = collector.get_data()
        probe.assert_called_once_with("192.0.2.10")
        self.assertIsNone(entry["error"])
        self.assertIsNone(entry["batteryLevel"])
        self.assertIsNone(entry["mediaTitle"])
        self.assertIsNone(entry["foregroundApp"])
        self.assertEqual(
            entry["transport"],
            {
                "active": "lan",
                "degraded": True,
                "online": True,
                "lastSeenAt": entry["transport"]["lastSeenAt"],
                "detail": "arp",
            },
        )

    def test_failure_with_ip_offline_degrades(self):
        fake = FakeAdb([EMPTY_DEVICES, EMPTY_DEVICES])
        collector, patcher = build(entry={"ip": "192.0.2.10"}, fake=fake)
        self.addCleanup(patcher.stop)
        with mock.patch.object(
            ad, "probe_lan", return_value=(False, "lan", None)
        ):
            entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["transport"]["active"], "lan")
        self.assertTrue(entry["transport"]["degraded"])
        self.assertFalse(entry["transport"]["online"])
        self.assertIsNone(entry["transport"]["lastSeenAt"])

    def test_fallback_false_raises_and_never_probes(self):
        fake = FakeAdb([EMPTY_DEVICES, EMPTY_DEVICES])
        collector, patcher = build(
            entry={"ip": "192.0.2.10", "fallback": False}, fake=fake
        )
        self.addCleanup(patcher.stop)
        with mock.patch.object(ad, "probe_lan") as probe:
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        probe.assert_not_called()
        self.assertIn("未检测到 Android 设备", str(ctx.exception))

    def test_no_ip_failure_raises(self):
        fake = FakeAdb([EMPTY_DEVICES])
        collector, patcher = build(fake=fake)
        self.addCleanup(patcher.stop)
        with mock.patch.object(ad, "probe_lan") as probe:
            with self.assertRaises(CollectorError):
                collector.get_data()
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()


class AdbBudgetTests(unittest.TestCase):
    """An already-started collector cannot be cancelled, so it has to bound its own
    subprocess calls. One Android attempt can chain ~46 s of timeouts, more than
    the daemon's 30 s kill, so without this the whole round dies and every device
    goes stale instead of just this one."""

    def test_no_deadline_means_no_clamp(self):
        collector, patcher = build()
        self.addCleanup(patcher.stop)
        self.assertFalse(collector.out_of_budget())
        self.assertEqual(collector.budget_timeout(8), 8)

    def test_budget_timeout_clamps_to_remaining(self):
        collector, patcher = build()
        self.addCleanup(patcher.stop)
        collector.deadline = time.monotonic() + 2.0
        clamped = collector.budget_timeout(8)
        self.assertLessEqual(clamped, 2.0)
        self.assertGreater(clamped, 0)

    def test_budget_timeout_raises_when_spent(self):
        collector, patcher = build()
        self.addCleanup(patcher.stop)
        collector.deadline = time.monotonic() - 1.0
        self.assertTrue(collector.out_of_budget())
        with self.assertRaises(ad.CollectorError) as ctx:
            collector.budget_timeout(8)
        self.assertIn(BUDGET_EXHAUSTED_MESSAGE, str(ctx.exception))

    def test_run_adb_clamps_the_subprocess_timeout(self):
        collector = ad.AndroidCollector(entry={"ip": "192.168.1.21"})
        collector.deadline = time.monotonic() + 3.0
        with mock.patch.object(ad.subprocess, "run") as run:
            run.return_value = mock.Mock(stdout="List of devices attached\n", returncode=0)
            collector._run_adb(["devices"], timeout=8, serial=False)
        self.assertEqual(run.call_count, 1)
        self.assertLessEqual(run.call_args.kwargs["timeout"], 3.0)

    def test_run_adb_refuses_to_start_when_spent(self):
        collector = ad.AndroidCollector(entry={"ip": "192.168.1.21"})
        collector.deadline = time.monotonic() - 1.0
        with mock.patch.object(ad.subprocess, "run") as run:
            with self.assertRaises(ad.CollectorError):
                collector._run_adb(["devices"], timeout=8, serial=False)
        self.assertEqual(run.call_count, 0, "no subprocess may start once the budget is gone")
