"""Offline tests for the iPhone fallback transport chain."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from collectors import iphone_collector as ip
from collectors.base import CollectorError

USB_ERROR = "未检测到 iPhone（USB 未连接或设备未解锁）"


async def usb_unavailable():
    raise CollectorError(USB_ERROR)


def usb_status(
    battery: int | None = 87,
    charging: bool | None = True,
    model: str | None = "iPhone15,3",
) -> ip._IPhoneStatus:
    return ip._IPhoneStatus(
        battery_level=battery,
        is_charging=charging,
        device_model=model,
    )


async def usb_ok() -> ip._IPhoneStatus:
    return usb_status()


class ConstructorTests(unittest.TestCase):
    def test_no_entry_keeps_existing_defaults(self):
        collector = ip.IPhoneCollector()
        self.assertIsNone(collector.ip)
        self.assertTrue(collector.fallback)
        self.assertFalse(collector.wifi_data_channel)

    def test_entry_options(self):
        collector = ip.IPhoneCollector(
            {"ip": "192.0.2.20", "fallback": False, "wifiDataChannel": True}
        )
        self.assertEqual(collector.ip, "192.0.2.20")
        self.assertFalse(collector.fallback)
        self.assertTrue(collector.wifi_data_channel)

    def test_invalid_ip_raises(self):
        with self.assertRaises(CollectorError) as ctx:
            ip.IPhoneCollector({"ip": "not-an-ip"})
        self.assertIn("IP 非法", str(ctx.exception))

    def test_non_dict_entry_uses_defaults(self):
        self.assertIsNone(ip.IPhoneCollector("oops").ip)


class UsbTests(unittest.TestCase):
    def test_usb_success_has_transport(self):
        collector = ip.IPhoneCollector()
        with mock.patch.object(collector, "_read", new=usb_ok):
            entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["batteryLevel"], 87)
        self.assertIs(entry["isCharging"], True)
        self.assertEqual(entry["model"], "iPhone15,3")
        self.assertEqual(entry["transport"]["active"], "usb")
        self.assertFalse(entry["transport"]["degraded"])
        self.assertTrue(entry["transport"]["online"])
        self.assertIsInstance(entry["transport"]["lastSeenAt"], int)
        self.assertEqual(entry["transport"]["detail"], "usb")

    def test_usb_empty_read_keeps_original_error_text(self):
        async def empty_read():
            return ip._IPhoneStatus()

        collector = ip.IPhoneCollector()
        with mock.patch.object(collector, "_read", new=empty_read):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        self.assertIn("USB 未连接", str(ctx.exception))


class LanFallbackTests(unittest.TestCase):
    def test_usb_failure_with_ip_reports_degraded_online(self):
        collector = ip.IPhoneCollector({"ip": "192.0.2.20"})
        transport = {
            "id": "iphone",
            "platform": "ios",
            "model": None,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "transport": {
                "active": "lan",
                "degraded": True,
                "online": True,
                "lastSeenAt": None,
                "detail": "arp",
            },
            "error": None,
        }
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(
                 ip, "probe_lan", return_value=(True, "arp", "aa:bb:cc:dd:ee:ff")
             ):
            entry = collector.get_data()
        self.assertIsInstance(entry["transport"]["lastSeenAt"], int)
        transport["transport"]["lastSeenAt"] = entry["transport"]["lastSeenAt"]
        self.assertEqual(entry, transport)

    def test_usb_failure_with_ip_offline_is_not_an_error(self):
        collector = ip.IPhoneCollector({"ip": "192.0.2.20"})
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(ip, "probe_lan", return_value=(False, "lan", None)):
            entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["transport"]["active"], "lan")
        self.assertTrue(entry["transport"]["degraded"])
        self.assertFalse(entry["transport"]["online"])
        self.assertIsNone(entry["transport"]["lastSeenAt"])
        self.assertIsNone(entry["batteryLevel"])
        self.assertEqual(entry["mediaState"], "unknown")
        json.dumps(entry, ensure_ascii=False)

    def test_fallback_false_raises_and_never_probes(self):
        collector = ip.IPhoneCollector({"ip": "192.0.2.20", "fallback": False})
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(ip, "probe_lan") as probe:
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        probe.assert_not_called()
        self.assertEqual(str(ctx.exception), USB_ERROR)

    def test_no_ip_raises(self):
        collector = ip.IPhoneCollector()
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(ip, "probe_lan") as probe:
            with self.assertRaises(CollectorError):
                collector.get_data()
        probe.assert_not_called()

    def test_lan_ignores_mac(self):
        collector = ip.IPhoneCollector({"ip": "192.0.2.20"})
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(ip, "probe_lan", return_value=(True, "ping", None)):
            entry = collector.get_data()
        self.assertEqual(entry["transport"]["detail"], "ping")


class WifiChannelTests(unittest.TestCase):
    def test_disabled_by_default_never_calls_wifi(self):
        collector = ip.IPhoneCollector({"ip": "192.0.2.20"})
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(collector, "_read_wifi") as wifi, \
             mock.patch.object(ip, "probe_lan", return_value=(False, "lan", None)):
            collector.get_data()
        wifi.assert_not_called()

    def test_wifi_battery_success_is_usb_wifi(self):
        collector = ip.IPhoneCollector({"wifiDataChannel": True})
        wifi_status = ip._IPhoneStatus(
            battery_level=55,
            is_charging=False,
            device_model="iPhone15,3",
            transport_detail="wifi:bonjour:192.0.2.20",
        )
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(
                 collector, "_read_wifi", return_value=wifi_status
             ), mock.patch.object(ip, "probe_lan") as probe:
            entry = collector.get_data()
        probe.assert_not_called()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["batteryLevel"], 55)
        self.assertEqual(entry["transport"]["active"], "usb-wifi")
        self.assertFalse(entry["transport"]["degraded"])
        self.assertTrue(entry["transport"]["online"])
        self.assertEqual(
            entry["transport"]["detail"], "wifi:bonjour:192.0.2.20"
        )

    def test_wifi_handshake_without_battery_falls_through_to_lan(self):
        collector = ip.IPhoneCollector(
            {"wifiDataChannel": True, "ip": "192.0.2.20"}
        )
        handshake_only = ip._IPhoneStatus(device_model="iPhone15,3")
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(
                 collector, "_read_wifi", return_value=handshake_only
             ), mock.patch.object(
                 ip, "probe_lan", return_value=(True, "arp", None)
             ) as probe:
            entry = collector.get_data()
        probe.assert_called_once_with("192.0.2.20")
        self.assertEqual(entry["transport"]["active"], "lan")
        self.assertTrue(entry["transport"]["degraded"])
        self.assertIsNone(entry["batteryLevel"])

    def test_wifi_failure_without_ip_raises_original_error(self):
        collector = ip.IPhoneCollector({"wifiDataChannel": True})
        with mock.patch.object(collector, "_read", new=usb_unavailable), \
             mock.patch.object(collector, "_read_wifi", return_value=None):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        self.assertEqual(str(ctx.exception), USB_ERROR)


class BatteryParsingTests(unittest.TestCase):
    def test_apply_battery_accepts_string_charging(self):
        status = ip._IPhoneStatus()
        ip.IPhoneCollector._apply_battery(
            status, {"CurrentCapacity": 10, "IsCharging": "true"}
        )
        self.assertEqual(status.battery_level, 10)
        self.assertIs(status.is_charging, True)

    def test_apply_battery_ignores_missing_fields(self):
        status = ip._IPhoneStatus()
        ip.IPhoneCollector._apply_battery(status, {})
        self.assertIsNone(status.battery_level)
        self.assertIsNone(status.is_charging)


if __name__ == "__main__":
    unittest.main()
