"""Offline tests for config loading and collect_all fault isolation."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import status_core
from collectors import console_collector as cc
from collectors.base import BaseCollector, CollectorError


class BoomCollector(BaseCollector):
    platform = "ios"
    device_id = "iphone"

    def __init__(self, *args, **kwargs):
        pass

    def get_data(self):
        raise RuntimeError("boom")


class BoomAndroid(BoomCollector):
    platform = "android"
    device_id = "android"


class Recorder:
    """Thread-safe concurrency/ordering recorder for fake collectors."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active = 0
        self.peak = 0
        self.starts: list[tuple[str, float]] = []
        self.ends: list[tuple[str, float]] = []


class FakeCollector(BaseCollector):
    platform = "linux"

    def __init__(self, device_id: str, delay: float = 0.1,
                 recorder: Recorder | None = None) -> None:
        self.device_id = device_id
        self.delay = delay
        self.recorder = recorder

    def get_data(self):
        if self.recorder is not None:
            with self.recorder.lock:
                self.recorder.active += 1
                self.recorder.peak = max(self.recorder.peak, self.recorder.active)
                self.recorder.starts.append((self.device_id, time.monotonic()))
            try:
                time.sleep(self.delay)
                return self._entry()
            finally:
                with self.recorder.lock:
                    self.recorder.active -= 1
                    self.recorder.ends.append((self.device_id, time.monotonic()))
        time.sleep(self.delay)
        return self._entry()

    def _entry(self):
        return {
            "id": self.device_id,
            "platform": self.platform,
            "model": self.device_id,
            "batteryLevel": None,
            "isCharging": None,
            "mediaTitle": None,
            "mediaArtist": None,
            "mediaState": "unknown",
            "foregroundApp": None,
            "foregroundAppName": None,
            "error": None,
        }


def run_with_collectors(collectors):
    with mock.patch.object(
        status_core, "build_collectors", return_value=(collectors, [])
    ):
        return status_core.collect_all(config={})


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_missing_file_returns_empty(self):
        self.assertEqual(status_core.load_config(self.dir / "nope.json"), {})

    def test_broken_file_returns_empty(self):
        path = self.dir / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(status_core.load_config(path), {})

    def test_non_object_root_returns_empty(self):
        path = self.dir / "list.json"
        path.write_text("[1,2]", encoding="utf-8")
        self.assertEqual(status_core.load_config(path), {})

    def test_bom_is_tolerated(self):
        path = self.dir / "bom.json"
        path.write_text('{"Consoles": []}', encoding="utf-8-sig")
        self.assertEqual(status_core.load_config(path), {"Consoles": []})


class CollectAllTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "console_state.json"
        self.arp = mock.patch.object(cc, "arp_probe", return_value=(True, None))
        self.arp.start()
        self.addCleanup(self.arp.stop)
        self.state_patch = mock.patch.object(cc, "STATE_FILE", self.state)
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
        for name, fake in (
            ("IPhoneCollector", BoomCollector),
            ("AndroidCollector", BoomAndroid),
        ):
            patcher = mock.patch.object(status_core, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_order_and_isolation_with_console(self):
        payload = status_core.collect_all(config={
            "Consoles": [{"id": "ps5", "name": "PS5", "ip": "192.0.2.10"}],
            "SshNodes": [],
        })
        self.assertEqual(payload["schema"], 3)
        self.assertIsInstance(payload["generatedAt"], int)
        ids = [device["id"] for device in payload["devices"]]
        self.assertEqual(ids, ["iphone", "android", "ps5"])
        self.assertEqual(payload["devices"][0]["error"], "boom")
        console = payload["devices"][2]
        self.assertIsNone(console["error"])
        self.assertTrue(console["console"]["online"])
        self.assertIsNone(console["console"]["statusText"])
        json.dumps(payload, ensure_ascii=False)

    def test_empty_config_keeps_v3_behaviour(self):
        payload = status_core.collect_all(config={})
        self.assertEqual([d["id"] for d in payload["devices"]], ["iphone", "android"])

    def test_invalid_console_entry_isolated(self):
        payload = status_core.collect_all(config={
            "Consoles": [{"id": "bad", "name": "no ip"}],
            "SshNodes": [{"id": "n", "user": "u", "host": "h", "platform": "windows"}],
        })
        ids = [device["id"] for device in payload["devices"]]
        self.assertIn("bad", ids)
        self.assertIn("n", ids)
        bad = next(d for d in payload["devices"] if d["id"] == "bad")
        self.assertIn("ip", bad["error"])
        node = next(d for d in payload["devices"] if d["id"] == "n")
        self.assertIn("平台", node["error"])


class PhoneConfigTests(unittest.TestCase):
    def test_phone_config_sections_are_passed_to_collectors(self):
        with mock.patch.object(status_core, "IPhoneCollector") as iphone, \
             mock.patch.object(status_core, "AndroidCollector") as android:
            collectors, invalid = status_core.build_collectors(
                {
                    "IPhone": {"ip": "192.0.2.20", "fallback": False},
                    "Android": {"ip": "192.0.2.21", "port": 5556},
                },
                adb="C:/adb.exe",
                serial="SER",
            )
        iphone.assert_called_once_with(
            entry={"ip": "192.0.2.20", "fallback": False}
        )
        android.assert_called_once_with(
            entry={"ip": "192.0.2.21", "port": 5556},
            adb="C:/adb.exe",
            serial="SER",
        )
        self.assertEqual(len(collectors), 2)
        self.assertEqual(invalid, [])

    def test_invalid_phone_config_becomes_invalid_entry(self):
        with mock.patch.object(
            status_core,
            "IPhoneCollector",
            side_effect=CollectorError("iPhone IP 非法：nope"),
        ), mock.patch.object(status_core, "AndroidCollector") as android:
            collectors, invalid = status_core.build_collectors(
                {"IPhone": {"ip": "nope"}, "Consoles": []}
            )
        android.assert_called_once_with(entry=None, adb=None, serial=None)
        self.assertEqual(len(collectors), 1)
        self.assertEqual(len(invalid), 1)
        entry = invalid[0]
        self.assertEqual(entry["id"], "iphone")
        self.assertEqual(entry["platform"], "ios")
        self.assertIn("iPhone IP 非法", entry["error"])
        self.assertEqual(entry["transport"]["active"], "none")
        self.assertFalse(entry["transport"]["online"])

    def test_invalid_phone_config_does_not_block_other_devices(self):
        collectors, invalid = status_core.build_collectors(
            {
                "IPhone": {"ip": "not-an-ip"},
                "Consoles": [{"id": "ps5", "ip": "192.0.2.10"}],
            }
        )
        self.assertEqual([c.device_id for c in collectors], ["android", "ps5"])
        self.assertEqual([entry["id"] for entry in invalid], ["iphone"])

    def test_no_phone_config_sections_keeps_defaults(self):
        collectors, invalid = status_core.build_collectors(
            {"IPhone": "not-a-dict", "Android": 5}
        )
        self.assertEqual([c.device_id for c in collectors], ["iphone", "android"])
        self.assertEqual(invalid, [])

    def test_empty_config_with_boom_collectors_still_has_transport(self):
        with mock.patch.object(status_core, "IPhoneCollector", BoomCollector), \
             mock.patch.object(status_core, "AndroidCollector", BoomAndroid):
            payload = status_core.collect_all(config={})
        self.assertEqual([d["id"] for d in payload["devices"]], ["iphone", "android"])
        for device in payload["devices"]:
            self.assertEqual(
                device["transport"],
                {
                    "active": "none",
                    "degraded": False,
                    "online": False,
                    "lastSeenAt": None,
                    "detail": None,
                },
            )


class BatchingTests(unittest.TestCase):
    def test_more_than_eight_collectors_keep_order_and_batch(self):
        recorder = Recorder()
        collectors = [
            FakeCollector(f"d{i}", delay=0.1, recorder=recorder) for i in range(10)
        ]
        payload = run_with_collectors(collectors)
        self.assertEqual(
            [device["id"] for device in payload["devices"]],
            [f"d{i}" for i in range(10)],
        )
        # At most eight probes run at once.
        self.assertLessEqual(recorder.peak, 8)
        self.assertGreaterEqual(recorder.peak, 2)
        # The second batch does not start before the first batch finished.
        first_batch_ends = [
            end for name, end in recorder.ends
            if name in {f"d{i}" for i in range(8)}
        ]
        second_batch_starts = [
            start for name, start in recorder.starts if name in {"d8", "d9"}
        ]
        self.assertEqual(len(second_batch_starts), 2)
        self.assertLessEqual(max(first_batch_ends), min(second_batch_starts) + 1e-3)

    def test_exactly_eight_collectors_run_in_parallel(self):
        recorder = Recorder()
        collectors = [
            FakeCollector(f"e{i}", delay=0.1, recorder=recorder) for i in range(8)
        ]
        payload = run_with_collectors(collectors)
        self.assertEqual([d["id"] for d in payload["devices"]],
                         [f"e{i}" for i in range(8)])
        self.assertLessEqual(recorder.peak, 8)
        self.assertGreaterEqual(recorder.peak, 2)

    def test_few_collectors_are_all_started_together(self):
        recorder = Recorder()
        collectors = [
            FakeCollector(f"f{i}", delay=0.1, recorder=recorder) for i in range(3)
        ]
        run_with_collectors(collectors)
        self.assertEqual(recorder.peak, 3)

    def test_collector_error_is_isolated(self):
        collectors = [FakeCollector("ok", delay=0.0), BoomCollector()]
        payload = run_with_collectors(collectors)
        self.assertEqual([d["id"] for d in payload["devices"]], ["ok", "iphone"])
        self.assertIsNone(payload["devices"][0]["error"])
        self.assertEqual(payload["devices"][1]["error"], "boom")

    def test_empty_collector_list_produces_no_devices(self):
        payload = run_with_collectors([])
        self.assertEqual(payload["devices"], [])


if __name__ == "__main__":
    unittest.main()
