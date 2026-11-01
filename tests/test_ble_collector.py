"""Fully offline tests for the BLE peripheral collector.

``bleak`` is never imported for real here: every collector-level test mocks
``ble_collector.scan_ble`` / ``ble_collector.read_ble_services``, and the lower
level scan/read tests inject a fake ``bleak`` module through ``sys.modules``.
The optional integration test only runs when ``STATUSCAST_BLE_INTEGRATION`` is
set, so the default suite never touches a radio.
"""

from __future__ import annotations

import json
import os
import sys
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

import status_core
from collectors import ble_collector as ble
from collectors.base import CollectorError

ADDRESS = "AA:BB:CC:DD:EE:FF"
OTHER_ADDRESS = "11:22:33:44:55:66"

SUCCESS_READING = {"connected": True, "battery": 85, "heart_rate": None}


def make_device(address: str, name: str | None = None) -> types.SimpleNamespace:
    return types.SimpleNamespace(address=address, name=name)


def make_advert(
    local_name: str | None = None, rssi: int | None = -60
) -> types.SimpleNamespace:
    return types.SimpleNamespace(local_name=local_name, rssi=rssi)


def scan_info(
    address: str = ADDRESS,
    *,
    name: str | None = None,
    local_name: str | None = None,
    rssi: int | None = -61,
) -> dict:
    return {
        "address": address.upper(),
        "name": name,
        "local_name": local_name,
        "rssi": rssi,
    }


def make_collector(**overrides) -> ble.BleDeviceCollector:
    entry = {"id": "dev", "name": "Device", "address": ADDRESS}
    entry.update(overrides)
    return ble.BleDeviceCollector(entry)


def fake_bleak_module(**client_config) -> types.ModuleType:
    """Build a fake ``bleak`` module whose client behaviour follows config."""

    class Client:
        instances: list["Client"] = []
        cfg = dict(client_config)

        def __init__(self, address, timeout=30.0, **kwargs):
            self.address = address
            self.timeout = timeout
            self.disconnected = False
            self.disconnect_raises = bool(Client.cfg.get("disconnect_error"))
            Client.instances.append(self)

        async def connect(self):
            if Client.cfg.get("connect_error"):
                raise RuntimeError("connect failed")

        async def disconnect(self):
            self.disconnected = True
            if self.disconnect_raises:
                raise RuntimeError("disconnect failed")

        async def read_gatt_char(self, uuid, **kwargs):
            if Client.cfg.get("read_error"):
                raise RuntimeError("read failed")
            return Client.cfg.get("battery_payload", b"\x55")

        async def start_notify(self, uuid, callback):
            payload = Client.cfg.get("hr_payload")
            if payload is not None:
                callback(None, payload)

        async def stop_notify(self, uuid):
            return None

    module = types.ModuleType("bleak")
    module.BleakClient = Client
    module.BleakScanner = types.SimpleNamespace(
        discovered=None,
        calls=[],
    )
    return module


class BleTestBase(unittest.TestCase):
    def setUp(self) -> None:
        ble.reset_scan_cache()
        self.addCleanup(ble.reset_scan_cache)


class ConfigValidationTests(BleTestBase):
    def test_missing_id_raises(self):
        for entry in ({"address": ADDRESS}, {"name": "x"}, {}):
            with self.subTest(entry=entry):
                with self.assertRaises(CollectorError) as ctx:
                    ble.BleDeviceCollector(entry)
                self.assertIn("id", str(ctx.exception))

    def test_missing_address_and_name_filter_raises(self):
        for entry in (
            {"id": "x"},
            {"id": "x", "address": "   ", "nameFilter": "  "},
        ):
            with self.subTest(entry=entry):
                with self.assertRaises(CollectorError) as ctx:
                    ble.BleDeviceCollector(entry)
                message = str(ctx.exception)
                self.assertIn("address", message)
                self.assertIn("nameFilter", message)

    def test_non_dict_entry_raises(self):
        with self.assertRaises(CollectorError):
            ble.BleDeviceCollector("nope")  # type: ignore[arg-type]

    def test_name_defaults_to_id_and_address_is_normalised(self):
        collector = ble.BleDeviceCollector(
            {"id": "airpods", "address": "aa:bb:cc:dd:ee:ff"}
        )
        self.assertEqual(collector.platform, "ble")
        self.assertEqual(collector.device_id, "airpods")
        self.assertEqual(collector.name, "airpods")
        self.assertEqual(collector.address, ADDRESS)
        self.assertEqual(collector.services, ["battery"])
        self.assertEqual(collector.heart_rate_wait, 3.0)

    def test_services_are_cleaned_and_fall_back_to_default(self):
        collector = make_collector(
            services=["heart_rate", "bogus", " HEART_RATE ", 5, "battery"]
        )
        self.assertEqual(collector.services, ["heart_rate", "battery"])

        for value in (["bogus"], [], "battery", None):
            with self.subTest(value=value):
                self.assertEqual(
                    ble.BleDeviceCollector(
                        {"id": "x", "address": ADDRESS, "services": value}
                    ).services,
                    ["battery"],
                )

    def test_heart_rate_wait_is_clamped_to_one_through_eight(self):
        cases = [
            (0, 1.0),
            (-5, 1.0),
            (1, 1.0),
            (2.5, 2.5),
            (8, 8.0),
            (99, 8.0),
            ("2", 2.0),
            ("nope", 3.0),
            (None, 3.0),
            (True, 3.0),
        ]
        for value, expected in cases:
            with self.subTest(value=value):
                collector = make_collector(heartRateWaitSeconds=value)
                self.assertEqual(collector.heart_rate_wait, expected)


class ScanParsingTests(BleTestBase):
    def _patch_bleak(self, module):
        return mock.patch.dict(sys.modules, {"bleak": module})

    def test_scan_ble_parses_discover_dict_with_advertisement(self):
        module = fake_bleak_module()

        async def discover(timeout=5.0, *, return_adv=False, **kwargs):
            module.BleakScanner.calls.append((timeout, return_adv))
            return {
                ADDRESS: (
                    make_device(ADDRESS, "OS name"),
                    make_advert("Polar H10", -50),
                )
            }

        module.BleakScanner.discover = discover
        with self._patch_bleak(module):
            result = ble.scan_ble(2.0)

        self.assertEqual(
            result,
            {
                ADDRESS: {
                    "address": ADDRESS,
                    "name": "OS name",
                    "local_name": "Polar H10",
                    "rssi": -50,
                }
            },
        )
        self.assertEqual(module.BleakScanner.calls, [(2.0, True)])

    def test_scan_ble_tolerates_the_list_form(self):
        module = fake_bleak_module()

        async def discover(timeout=5.0, *, return_adv=False, **kwargs):
            return [
                (make_device("aa:bb:cc:dd:ee:ff", "buds"), make_advert(None, -70)),
                make_device(OTHER_ADDRESS, "band"),
            ]

        module.BleakScanner.discover = discover
        with self._patch_bleak(module):
            result = ble.scan_ble()

        self.assertEqual(result[ADDRESS]["address"], ADDRESS)
        self.assertEqual(result[ADDRESS]["name"], "buds")
        self.assertEqual(result[ADDRESS]["rssi"], -70)
        self.assertEqual(result[OTHER_ADDRESS]["name"], "band")
        self.assertIsNone(result[OTHER_ADDRESS]["rssi"])

    def test_scan_failure_degrades_to_empty_mapping(self):
        module = fake_bleak_module()

        async def discover(timeout=5.0, *, return_adv=False, **kwargs):
            raise RuntimeError("radio off")

        module.BleakScanner.discover = discover
        with self._patch_bleak(module):
            self.assertEqual(ble.scan_ble(), {})

    def test_missing_bleak_raises_the_fixed_message(self):
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(CollectorError) as ctx:
                ble.scan_ble()
        self.assertEqual(str(ctx.exception), "缺少 BLE 依赖：bleak")

    def test_battery_payload_parsing(self):
        cases = {
            b"\x55": 85,
            b"\x00": 0,
            b"\x64": 100,
            b"\x65": None,
            b"": None,
            None: None,
            b"\x55\x01": 85,  # first byte only
        }
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                self.assertEqual(ble._parse_battery_level(payload), expected)

    def test_heart_rate_payload_parsing(self):
        cases = {
            bytes([0x00, 72]): 72,
            bytes([0x01, 0x2C, 0x01]): 300,
            bytes([0x00]): None,
            bytes([0x01, 0x2C]): None,
            b"": None,
            None: None,
        }
        for payload, expected in cases.items():
            with self.subTest(payload=payload):
                self.assertEqual(ble._parse_heart_rate(payload), expected)


class ReadServicesTests(BleTestBase):
    def _read(self, services, client_config=None, wait=0.02):
        module = fake_bleak_module(**(client_config or {}))
        with mock.patch.dict(sys.modules, {"bleak": module}):
            result = ble.read_ble_services(ADDRESS, services, wait)
        return result, module

    def test_battery_read(self):
        result, module = self._read(["battery"], {"battery_payload": b"\x55"})
        self.assertEqual(
            result, {"connected": True, "battery": 85, "heart_rate": None}
        )
        client = module.BleakClient.instances[0]
        self.assertEqual(client.address, ADDRESS)
        self.assertEqual(client.timeout, 5.0)
        self.assertTrue(client.disconnected)

    def test_battery_out_of_range_is_none_but_connected(self):
        for payload in (b"\x65", b""):
            with self.subTest(payload=payload):
                result, _ = self._read(
                    ["battery"], {"battery_payload": payload}
                )
                self.assertTrue(result["connected"])
                self.assertIsNone(result["battery"])

    def test_battery_read_error_is_none_but_connected(self):
        result, _ = self._read(["battery"], {"read_error": True})
        self.assertTrue(result["connected"])
        self.assertIsNone(result["battery"])

    def test_heart_rate_notification(self):
        result, _ = self._read(
            ["heart_rate"], {"hr_payload": bytes([0x00, 72])}
        )
        self.assertEqual(
            result, {"connected": True, "battery": None, "heart_rate": 72}
        )

    def test_heart_rate_uint16_notification(self):
        result, _ = self._read(
            ["heart_rate"], {"hr_payload": bytes([0x01, 0x2C, 0x01])}
        )
        self.assertEqual(result["heart_rate"], 300)

    def test_heart_rate_timeout_is_not_an_error(self):
        result, _ = self._read(["heart_rate"], {}, wait=0.02)
        self.assertTrue(result["connected"])
        self.assertIsNone(result["heart_rate"])

    def test_both_services(self):
        result, _ = self._read(
            ["battery", "heart_rate"],
            {"battery_payload": b"\x64", "hr_payload": bytes([0x00, 88])},
        )
        self.assertEqual(
            result, {"connected": True, "battery": 100, "heart_rate": 88}
        )

    def test_connect_failure_never_raises(self):
        result, _ = self._read(["battery"], {"connect_error": True})
        self.assertEqual(
            result, {"connected": False, "battery": None, "heart_rate": None}
        )

    def test_disconnect_failure_is_swallowed(self):
        result, _ = self._read(
            ["battery"], {"battery_payload": b"\x32", "disconnect_error": True}
        )
        self.assertTrue(result["connected"])
        self.assertEqual(result["battery"], 50)

    def test_missing_dependency_never_raises(self):
        with mock.patch.dict(sys.modules, {"bleak": None}):
            result = ble.read_ble_services(ADDRESS, ["battery"], 0.02)
        self.assertEqual(
            result, {"connected": False, "battery": None, "heart_rate": None}
        )


class ScanCacheTests(BleTestBase):
    def test_two_collectors_in_one_round_share_a_single_scan(self):
        scan = {
            ADDRESS: scan_info(ADDRESS, local_name="AirPods"),
            OTHER_ADDRESS: scan_info(OTHER_ADDRESS, local_name="Band"),
        }
        first = make_collector(id="a", address=ADDRESS)
        second = make_collector(id="b", address=OTHER_ADDRESS)
        with mock.patch.object(ble, "scan_ble", return_value=scan) as scan_mock, \
             mock.patch.object(
                 ble, "read_ble_services", return_value=SUCCESS_READING
             ) as read_mock:
            first.get_data()
            second.get_data()
        self.assertEqual(scan_mock.call_count, 1)
        self.assertEqual(
            [call.args[0] for call in read_mock.call_args_list],
            [ADDRESS, OTHER_ADDRESS],
        )

    def test_cache_expires_after_ttl(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="AirPods")}
        collector = make_collector()
        with mock.patch.object(ble, "scan_ble", return_value=scan) as scan_mock, \
             mock.patch.object(ble, "read_ble_services", return_value=SUCCESS_READING):
            collector.get_data()
            self.assertEqual(scan_mock.call_count, 1)
            ble._SCAN_CACHE_AT = (
                time.monotonic() - ble.SCAN_CACHE_TTL_SECONDS - 1
            )
            collector.get_data()
            self.assertEqual(scan_mock.call_count, 2)

    def test_concurrent_collectors_scan_once(self):
        scan = {
            ADDRESS: scan_info(ADDRESS, local_name="AirPods"),
            OTHER_ADDRESS: scan_info(OTHER_ADDRESS, local_name="Band"),
        }
        first = make_collector(id="a", address=ADDRESS)
        second = make_collector(id="b", address=OTHER_ADDRESS)
        calls = {"scan": 0}

        def slow_scan(_timeout=ble.SCAN_TIMEOUT_SECONDS):
            calls["scan"] += 1
            time.sleep(0.05)
            return scan

        with mock.patch.object(ble, "scan_ble", side_effect=slow_scan), \
             mock.patch.object(ble, "read_ble_services", return_value=SUCCESS_READING):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda c: c.get_data(), (first, second)))
        self.assertEqual(calls["scan"], 1)
        self.assertTrue(all(entry["transport"]["online"] for entry in results))


class CollectorSemanticsTests(BleTestBase):
    def _run(self, entry, scan, reading=None):
        with mock.patch.object(ble, "scan_ble", return_value=scan), \
             mock.patch.object(
                 ble, "read_ble_services", return_value=reading or SUCCESS_READING
             ) as read_mock:
            data = ble.BleDeviceCollector(entry).get_data()
        return data, read_mock

    def test_success_entry_shape(self):
        entry = {
            "id": "airpods",
            "name": "AirPods Pro",
            "address": "aa:bb:cc:dd:ee:ff",
        }
        scan = {
            ADDRESS: scan_info(
                ADDRESS, name="AirPods", local_name="AirPods Pro", rssi=-61
            )
        }
        data, read_mock = self._run(entry, scan)

        self.assertEqual(data["id"], "airpods")
        self.assertEqual(data["platform"], "ble")
        self.assertEqual(data["model"], "AirPods Pro")
        self.assertEqual(data["batteryLevel"], 85)
        self.assertIsNone(data["isCharging"])
        self.assertIsNone(data["mediaTitle"])
        self.assertIsNone(data["mediaArtist"])
        self.assertEqual(data["mediaState"], "unknown")
        self.assertIsNone(data["foregroundApp"])
        self.assertIsNone(data["foregroundAppName"])
        self.assertIsNone(data["error"])

        self.assertEqual(
            set(data["ble"]),
            {
                "address",
                "name",
                "rssi",
                "services",
                "connected",
                "heartRate",
                "statusText",
                "lastSeenAt",
            },
        )
        self.assertEqual(data["ble"]["address"], ADDRESS)
        self.assertEqual(data["ble"]["name"], "AirPods Pro")
        self.assertEqual(data["ble"]["rssi"], -61)
        self.assertEqual(data["ble"]["services"], ["battery"])
        self.assertTrue(data["ble"]["connected"])
        self.assertIsNone(data["ble"]["heartRate"])
        self.assertIsNone(data["ble"]["statusText"])
        self.assertIsInstance(data["ble"]["lastSeenAt"], int)

        self.assertEqual(
            set(data["transport"]),
            {"active", "degraded", "online", "lastSeenAt", "detail"},
        )
        self.assertEqual(data["transport"]["active"], "ble")
        self.assertFalse(data["transport"]["degraded"])
        self.assertTrue(data["transport"]["online"])
        self.assertEqual(
            data["transport"]["lastSeenAt"], data["ble"]["lastSeenAt"]
        )
        self.assertEqual(data["transport"]["detail"], ADDRESS)

        read_mock.assert_called_once_with(
            ADDRESS, ["battery"], 3.0
        )
        json.dumps(data, ensure_ascii=False)

    def test_address_matching_is_case_insensitive(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="AirPods")}
        data, read_mock = self._run(
            {"id": "a", "address": "aa:bb:cc:dd:ee:ff"}, scan
        )
        self.assertTrue(data["transport"]["online"])
        read_mock.assert_called_once_with(ADDRESS, ["battery"], 3.0)

    def test_name_filter_matches_local_name(self):
        scan = {
            OTHER_ADDRESS: scan_info(
                OTHER_ADDRESS, name="OS name", local_name="Polar H10"
            )
        }
        data, read_mock = self._run(
            {"id": "hrm", "nameFilter": "polar", "services": ["heart_rate"]},
            scan,
            reading={"connected": True, "battery": None, "heart_rate": 72},
        )
        self.assertTrue(data["transport"]["online"])
        self.assertEqual(data["ble"]["address"], OTHER_ADDRESS)
        self.assertEqual(data["ble"]["heartRate"], 72)
        self.assertIsNone(data["batteryLevel"])
        self.assertFalse(data["transport"]["degraded"])
        read_mock.assert_called_once_with(OTHER_ADDRESS, ["heart_rate"], 3.0)

    def test_name_filter_falls_back_to_device_name(self):
        scan = {
            OTHER_ADDRESS: scan_info(
                OTHER_ADDRESS, name="Polar H10", local_name=None
            )
        }
        data, _ = self._run({"id": "hrm", "nameFilter": "POLAR"}, scan)
        self.assertTrue(data["transport"]["online"])
        self.assertEqual(data["ble"]["name"], "Polar H10")

    def test_name_filter_miss_means_not_found(self):
        scan = {OTHER_ADDRESS: scan_info(OTHER_ADDRESS, local_name="AirPods")}
        data, read_mock = self._run({"id": "hrm", "nameFilter": "Polar"}, scan)
        read_mock.assert_not_called()
        self.assertFalse(data["transport"]["online"])
        self.assertEqual(data["ble"]["statusText"], "未发现")

    def test_not_found_entry(self):
        data, read_mock = self._run({"id": "a", "name": "Buds", "address": ADDRESS}, {})
        read_mock.assert_not_called()
        self.assertIsNone(data["error"])
        self.assertIsNone(data["batteryLevel"])
        self.assertFalse(data["ble"]["connected"])
        self.assertFalse(data["transport"]["online"])
        self.assertFalse(data["transport"]["degraded"])
        self.assertEqual(data["ble"]["statusText"], "未发现")
        self.assertIsNone(data["ble"]["lastSeenAt"])
        self.assertEqual(data["ble"]["address"], ADDRESS)
        self.assertIsNone(data["ble"]["name"])
        self.assertIsNone(data["ble"]["rssi"])
        self.assertIsNone(data["transport"]["lastSeenAt"])
        self.assertEqual(data["transport"]["detail"], ADDRESS)
        self.assertEqual(data["transport"]["active"], "ble")

    def test_not_found_with_name_filter_has_no_address(self):
        data, _ = self._run({"id": "a", "nameFilter": "Polar"}, {})
        self.assertIsNone(data["ble"]["address"])
        self.assertIsNone(data["transport"]["detail"])

    def test_connect_failure_is_online_but_degraded(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="AirPods")}
        data, _ = self._run(
            {"id": "a", "address": ADDRESS},
            scan,
            reading={"connected": False, "battery": None, "heart_rate": None},
        )
        self.assertIsNone(data["error"])
        self.assertTrue(data["transport"]["online"])
        self.assertTrue(data["transport"]["degraded"])
        self.assertFalse(data["ble"]["connected"])
        self.assertEqual(data["ble"]["statusText"], "已发现未连接")
        self.assertIsNone(data["batteryLevel"])
        self.assertIsNone(data["ble"]["heartRate"])
        self.assertIsInstance(data["ble"]["lastSeenAt"], int)

    def test_requested_battery_unreadable_is_degraded(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="AirPods")}
        data, _ = self._run(
            {"id": "a", "address": ADDRESS},
            scan,
            reading={"connected": True, "battery": None, "heart_rate": None},
        )
        self.assertTrue(data["ble"]["connected"])
        self.assertTrue(data["transport"]["degraded"])
        self.assertIsNone(data["ble"]["statusText"])
        self.assertIsNone(data["batteryLevel"])
        self.assertIsNone(data["error"])

    def test_requested_heart_rate_timeout_is_degraded_not_error(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="HRM")}
        data, _ = self._run(
            {"id": "hrm", "address": ADDRESS, "services": ["heart_rate"]},
            scan,
            reading={"connected": True, "battery": None, "heart_rate": None},
        )
        self.assertTrue(data["ble"]["connected"])
        self.assertTrue(data["transport"]["degraded"])
        self.assertIsNone(data["ble"]["heartRate"])
        self.assertIsNone(data["error"])

    def test_heart_rate_success_is_not_degraded(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="HRM")}
        data, _ = self._run(
            {"id": "hrm", "address": ADDRESS, "services": ["heart_rate"]},
            scan,
            reading={"connected": True, "battery": None, "heart_rate": 72},
        )
        self.assertFalse(data["transport"]["degraded"])
        self.assertEqual(data["ble"]["heartRate"], 72)
        self.assertIsNone(data["ble"]["statusText"])

    def test_missing_bleak_raises_and_becomes_an_error_entry(self):
        collector = make_collector()
        with mock.patch.dict(sys.modules, {"bleak": None}):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
            error_entry = status_core.run_collector(collector)
        self.assertEqual(str(ctx.exception), "缺少 BLE 依赖：bleak")
        self.assertEqual(error_entry["id"], "dev")
        self.assertEqual(error_entry["platform"], "ble")
        self.assertEqual(error_entry["error"], "缺少 BLE 依赖：bleak")
        self.assertEqual(error_entry["transport"]["active"], "none")
        self.assertFalse(error_entry["transport"]["online"])


class StatusCoreBleTests(BleTestBase):
    def test_ble_devices_are_instantiated_in_order(self):
        calls: list[dict] = []

        class Recorder:
            platform = "ble"

            def __init__(self, entry):
                self.device_id = entry["id"]
                calls.append(entry)

        entries = [
            {"id": "airpods", "address": ADDRESS},
            {"id": "hrm", "nameFilter": "Polar"},
        ]
        with mock.patch.object(status_core, "BleDeviceCollector", Recorder):
            collectors, invalid = status_core.build_collectors(
                {"BleDevices": entries}
            )
        self.assertEqual(calls, entries)
        self.assertEqual(
            [collector.device_id for collector in collectors],
            ["iphone", "android", "airpods", "hrm"],
        )
        self.assertEqual(invalid, [])

    def test_invalid_ble_entries_are_isolated(self):
        collectors, invalid = status_core.build_collectors(
            {
                "BleDevices": [
                    {"name": "no id"},
                    {"id": "no-target"},
                    {"id": "ok", "nameFilter": "Polar"},
                ]
            }
        )
        self.assertEqual(
            [collector.device_id for collector in collectors],
            ["iphone", "android", "ok"],
        )
        self.assertEqual(
            [(entry["id"], entry["platform"]) for entry in invalid],
            [("ble", "ble"), ("no-target", "ble")],
        )
        self.assertIn("id", invalid[0]["error"])
        self.assertIn("address", invalid[1]["error"])
        self.assertEqual(invalid[0]["transport"]["active"], "none")

    def test_missing_or_non_list_ble_devices_is_ignored(self):
        for config in ({}, {"BleDevices": None}, {"BleDevices": "nope"}):
            with self.subTest(config=config):
                collectors, invalid = status_core.build_collectors(config)
                self.assertEqual(
                    [collector.device_id for collector in collectors],
                    ["iphone", "android"],
                )
                self.assertEqual(invalid, [])

    def test_non_dict_ble_item_becomes_invalid_entry(self):
        collectors, invalid = status_core.build_collectors(
            {"BleDevices": ["nope"]}
        )
        self.assertEqual(
            [collector.device_id for collector in collectors], ["iphone", "android"]
        )
        self.assertEqual(len(invalid), 1)
        self.assertEqual(invalid[0]["platform"], "ble")
        self.assertIn("对象", invalid[0]["error"])

    def test_empty_config_keeps_existing_behaviour(self):
        collectors, invalid = status_core.build_collectors({})
        self.assertEqual(
            [collector.device_id for collector in collectors], ["iphone", "android"]
        )
        self.assertEqual(invalid, [])

    def test_ble_runs_after_console_and_ssh_collectors(self):
        collectors, _ = status_core.build_collectors(
            {
                "Consoles": [{"id": "ps5", "ip": "192.0.2.10"}],
                "SshNodes": [
                    {
                        "id": "mac",
                        "user": "u",
                        "host": "192.0.2.11",
                        "platform": "macos",
                    }
                ],
                "BleDevices": [{"id": "band", "address": ADDRESS}],
            }
        )
        self.assertEqual(
            [collector.device_id for collector in collectors],
            ["iphone", "android", "ps5", "mac", "band"],
        )

    def test_collect_all_returns_ble_entry(self):
        scan = {ADDRESS: scan_info(ADDRESS, local_name="AirPods")}
        with mock.patch.object(status_core, "IPhoneCollector") as iphone, \
             mock.patch.object(status_core, "AndroidCollector") as android, \
             mock.patch.object(ble, "scan_ble", return_value=scan), \
             mock.patch.object(
                 ble, "read_ble_services", return_value=SUCCESS_READING
             ):
            iphone.side_effect = CollectorError("no iphone")
            android.side_effect = CollectorError("no android")
            payload = status_core.collect_all(
                config={"BleDevices": [{"id": "airpods", "address": ADDRESS}]}
            )
        ids = [device["id"] for device in payload["devices"]]
        self.assertEqual(ids, ["airpods", "iphone", "android"])
        entry = payload["devices"][0]
        self.assertEqual(entry["platform"], "ble")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["batteryLevel"], 85)
        self.assertEqual(entry["transport"]["active"], "ble")


@unittest.skipUnless(
    os.environ.get("STATUSCAST_BLE_INTEGRATION"),
    "set STATUSCAST_BLE_INTEGRATION=1 to exercise a real BLE scan",
)
class BleIntegrationTests(unittest.TestCase):
    def test_real_scan_returns_a_mapping(self):
        devices = ble.scan_ble(2.0)
        self.assertIsInstance(devices, dict)
        for address, info in devices.items():
            self.assertEqual(address, address.upper())
            self.assertEqual(info["address"], address)


if __name__ == "__main__":
    unittest.main()
