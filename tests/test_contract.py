"""Schema-3 contract tests: collector outputs vs schema/status-contract.json.

The contract file is the single source of truth shared with the C# test project
(`tests-csharp/.../StatusPayloadTests.cs`). If a collector adds a field or an
additive object without updating the contract, this test fails on the Python
side; the C# tests fail on the other side.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import status_core
from collectors import android_collector as ac
from collectors import ble_collector as bc
from collectors import console_collector as cc
from collectors import iphone_collector as ic
from collectors import ssh_node_collector as sc
from collectors.base import CollectorError, error_entry

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads(
    (REPO_ROOT / "schema" / "status-contract.json").read_text(encoding="utf-8")
)


class ContractTestCase(unittest.TestCase):
    def assert_conforms(self, device: dict) -> None:
        standard = set(CONTRACT["collectorDeviceFields"])
        additive = set(CONTRACT["additiveObjects"])

        missing = standard - set(device)
        self.assertEqual(missing, set(), f"missing standard fields: {missing}")
        unknown = set(device) - standard - additive
        self.assertEqual(unknown, set(), f"unknown device keys: {unknown}")

        if "transport" in device:
            self.assertEqual(
                set(device["transport"]),
                set(CONTRACT["additiveFields"]["transport"]),
                "transport object does not match the contract",
            )

        for name in ("console", "node", "ble"):
            if name in device:
                unknown_nested = set(device[name]) - set(
                    CONTRACT["additiveFields"][name]
                )
                self.assertEqual(
                    unknown_nested, set(), f"unknown {name} keys: {unknown_nested}"
                )

    def console_payload(self) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            collector = cc.ConsoleCollector(
                {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.0.2.10"},
                state_path=Path(directory) / "state.json",
                now=1000,
            )
            with mock.patch.object(cc, "arp_probe", return_value=(True, "aa:bb:cc:dd:ee:ff")):
                return collector.get_data()

    def ssh_payload(self) -> dict:
        collector = sc.SshNodeCollector(
            {
                "id": "node",
                "user": "u",
                "host": "192.0.2.20",
                "platform": "linux",
            },
            now=1000,
        )
        values = {
            "battery": "80",
            "charging": "Charging",
            "model": "ThinkPad",
            "hostname": "node",
            "uptime": "3600",
        }
        with mock.patch.object(collector, "_ssh_values", return_value=values):
            return collector.get_data()

    def ble_payload(self) -> dict:
        collector = bc.BleDeviceCollector(
            {"id": "band", "name": "Band", "address": "AA:BB:CC:DD:EE:FF"}
        )
        scan = {
            "AA:BB:CC:DD:EE:FF": {
                "address": "AA:BB:CC:DD:EE:FF",
                "name": "Band",
                "local_name": "Band",
                "rssi": -60,
            }
        }
        with mock.patch.object(bc, "_get_scan", return_value=scan), mock.patch.object(
            bc,
            "read_ble_services",
            return_value={"connected": True, "battery": 77, "heart_rate": None},
        ):
            return collector.get_data()

    def iphone_payload(self) -> dict:
        status = ic._IPhoneStatus(
            battery_level=87,
            is_charging=True,
            device_model="iPhone15,3",
            transport_detail="usb",
        )
        with mock.patch.object(
            ic.IPhoneCollector, "_read", new=mock.AsyncMock(return_value=status)
        ):
            return ic.IPhoneCollector().get_data()

    def android_payload(self) -> dict:
        collector = ac.AndroidCollector()
        with mock.patch.object(collector, "_pick_serial", return_value="ABC123"), \
             mock.patch.object(collector, "_get_model", return_value="Pixel 7"), \
             mock.patch.object(collector, "_get_battery", return_value=(62, False)), \
             mock.patch.object(collector, "_get_foreground_app", return_value=None), \
             mock.patch.object(
                 collector, "_get_media", return_value=(None, None, "unknown")
             ):
            return collector._collect_primary()


class CollectorOutputContractTests(ContractTestCase):
    def test_every_payload_conforms_to_contract(self):
        payloads = {
            "console": self.console_payload(),
            "ssh": self.ssh_payload(),
            "ble": self.ble_payload(),
            "iphone": self.iphone_payload(),
            "android": self.android_payload(),
        }
        for name, payload in payloads.items():
            with self.subTest(collector=name):
                self.assert_conforms(payload)

    def test_every_additive_object_is_exercised(self):
        payloads = [
            self.console_payload(),
            self.ssh_payload(),
            self.ble_payload(),
        ]
        produced = {name for payload in payloads for name in ("console", "node", "ble", "transport") if name in payload}
        self.assertEqual(produced, set(CONTRACT["additiveObjects"]))

    def test_transport_shape_is_identical_everywhere(self):
        expected = set(CONTRACT["additiveFields"]["transport"])
        for payload in (self.console_payload(), self.iphone_payload()):
            with self.subTest(platform=payload["platform"]):
                self.assertEqual(set(payload["transport"]), expected)


class ErrorEntryContractTests(ContractTestCase):
    def test_error_entry_conforms_and_matches_collector_payload(self):
        entry = error_entry("ios", "iphone", "boom")
        self.assert_conforms(entry)
        self.assertFalse(entry["transport"]["online"])
        self.assertEqual(entry["transport"]["active"], "none")

    def test_invalid_config_entry_uses_the_same_shape(self):
        entry = status_core._invalid_entry("console", "ps5", "bad ip")
        self.assert_conforms(entry)
        self.assertEqual(
            set(entry), set(error_entry("console", "ps5", "x"))
        )

    def test_dispatcher_isolates_invalid_entries(self):
        payload = status_core.collect_all(
            config={
                "Consoles": [{"id": "bad"}],
                "SshNodes": [{"id": "n", "user": "u", "host": "h", "platform": "windows"}],
                "BleDevices": [{"id": "b"}],
            }
        )
        for device in payload["devices"]:
            with self.subTest(device=device["id"]):
                self.assert_conforms(device)
        invalid = {d["id"] for d in payload["devices"] if d["error"]}
        self.assertTrue({"bad", "n", "b"} <= invalid)


if __name__ == "__main__":
    unittest.main()
