"""Offline unittest fixtures for the C1 LAN console collector."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from collectors import console_collector as cc
from collectors import lan_probe as lp
from collectors.base import CollectorError

ENTRY = {"id": "ps5", "name": "PS5", "type": "ps5", "ip": "192.0.2.10"}


def make(state_path: Path, **overrides) -> cc.ConsoleCollector:
    entry = dict(ENTRY)
    entry.update(overrides)
    return cc.ConsoleCollector(entry, state_path=state_path, now=1000)


def read_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class ConfigValidationTests(unittest.TestCase):
    def test_missing_or_invalid_fields_raise(self):
        bad_entries = (
            {"name": "x"},
            {"id": "x"},
            {"id": "x", "ip": "not-an-ip"},
            {},
        )
        for bad in bad_entries:
            with self.subTest(bad=bad):
                with self.assertRaises(CollectorError):
                    cc.ConsoleCollector(bad, state_path=Path("unused.json"))

    def test_offline_text_is_optional_and_stripped(self):
        collector = make(Path("unused.json"), offlineText="  客厅主机离线  ")
        self.assertEqual(collector.offline_text, "客厅主机离线")
        blank = make(Path("unused.json"), offlineText="   ")
        self.assertIsNone(blank.offline_text)


class HysteresisTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "console_state.json"

    def test_offline_needs_two_consecutive_failures(self):
        with mock.patch.object(cc, "arp_probe", return_value=(True, "aa:bb:cc:dd:ee:ff")):
            entry = make(self.state).get_data()
        self.assertIsNone(entry["error"])
        self.assertTrue(entry["console"]["online"])
        self.assertIsNone(entry["console"]["statusText"])
        self.assertEqual(entry["model"], "PS5")
        self.assertEqual(entry["console"]["mac"], "aa:bb:cc:dd:ee:ff")

        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            first = make(self.state).get_data()
            self.assertIsNone(first["error"])      # streak 1: still online
            self.assertTrue(first["console"]["online"])
            self.assertIsNone(first["console"]["statusText"])

            second = make(self.state).get_data()   # streak 2: offline now
            self.assertIsNone(second["error"])
            self.assertFalse(second["console"]["online"])
            self.assertEqual(second["console"]["statusText"], cc.DEFAULT_OFFLINE_TEXT)
            self.assertEqual(second["model"], "PS5")

        with mock.patch.object(cc, "arp_probe", return_value=(True, None)):
            back = make(self.state).get_data()
            self.assertIsNone(back["error"])       # one success is online again
            self.assertTrue(back["console"]["online"])
            self.assertIsNone(back["console"]["statusText"])

    def test_never_seen_console_is_offline_immediately(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            entry = make(self.state).get_data()
        self.assertIsNone(entry["error"])
        self.assertFalse(entry["console"]["online"])
        self.assertEqual(entry["console"]["statusText"], "离线")
        self.assertEqual(entry["platform"], "console")

    def test_custom_offline_text(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            entry = make(self.state, offlineText="客厅主机离线").get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["console"]["statusText"], "客厅主机离线")

    def test_blank_offline_text_falls_back_to_default(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            entry = make(self.state, offlineText="   ").get_data()
        self.assertEqual(entry["console"]["statusText"], cc.DEFAULT_OFFLINE_TEXT)

    def test_arp_miss_falls_back_to_ping(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=True):
            entry = make(self.state).get_data()
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["console"]["method"], "ping")

    def test_state_file_survives_garbage(self):
        self.state.write_text("{not json", encoding="utf-8")
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            entry = make(self.state).get_data()
        self.assertIsNone(entry["error"])
        self.assertFalse(entry["console"]["online"])

    def test_multiple_consoles_have_independent_state(self):
        xbox = {"id": "xbox", "name": "Xbox Series X", "type": "xbox", "ip": "192.0.2.11"}
        with mock.patch.object(cc, "arp_probe", return_value=(True, None)):
            make(self.state).get_data()           # ps5 online
            make(self.state, **xbox).get_data()   # xbox online
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            ps5 = make(self.state).get_data()
            self.assertIsNone(ps5["error"])
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(False, None)):
            xbox_entry = make(self.state, **xbox).get_data()
            self.assertIsNone(xbox_entry["error"])
        # First miss for each: both stay online.
        self.assertTrue(ps5["console"]["online"])
        self.assertTrue(xbox_entry["console"]["online"])

    def test_payload_is_json_serialisable(self):
        with mock.patch.object(cc, "arp_probe", return_value=(True, None)):
            payload = make(self.state).get_data()
        json.dumps(payload, ensure_ascii=False)

    def test_device_entry_shape(self):
        with mock.patch.object(cc, "arp_probe", return_value=(True, None)):
            entry = make(self.state).get_data()
        self.assertEqual(
            set(entry),
            {
                "id", "platform", "model", "batteryLevel", "isCharging",
                "mediaTitle", "mediaArtist", "mediaState", "foregroundApp",
                "foregroundAppName", "console", "transport", "error",
            },
        )
        self.assertEqual(
            set(entry["console"]),
            {
                "online", "method", "lastSeenAt", "mac", "idleHint",
                "name", "type", "statusText",
            },
        )
        self.assertIsNone(entry["error"])


class TcpFallbackTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "console_state.json"

    def test_tcp_hit_after_arp_and_ping_miss(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(True, 3074)) as tcp:
            entry = make(self.state).get_data()
        tcp.assert_called_once_with("192.0.2.10", (9295, 3074, 80, 443))
        self.assertIsNone(entry["error"])
        self.assertTrue(entry["console"]["online"])
        self.assertEqual(entry["console"]["method"], "tcp")
        self.assertIsNone(entry["console"]["mac"])
        self.assertEqual(entry["console"]["lastSeenAt"], 1000)
        self.assertEqual(
            entry["transport"],
            {
                "active": "tcp",
                "degraded": False,
                "online": True,
                "lastSeenAt": 1000,
                "detail": "tcp:3074",
            },
        )

    def test_fallback_false_never_tries_tcp(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(True, 80)) as tcp:
            entry = make(self.state, fallback=False).get_data()
        tcp.assert_not_called()
        self.assertFalse(entry["console"]["online"])
        self.assertEqual(entry["console"]["method"], "lan")
        self.assertEqual(entry["transport"]["active"], "none")
        self.assertFalse(entry["transport"]["online"])
        self.assertIsNone(entry["transport"]["lastSeenAt"])

    def test_custom_tcp_ports_are_sanitized_and_used(self):
        entry = make(
            self.state, tcpPorts=[0, 70000, "443", True, 9295, 9295]
        )
        self.assertEqual(entry.tcp_ports, (443, 9295))
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe", return_value=(True, 443)) as tcp:
            result = entry.get_data()
        tcp.assert_called_once_with("192.0.2.10", (443, 9295))
        self.assertEqual(result["console"]["method"], "tcp")

    def test_empty_tcp_port_list_disables_tcp(self):
        collector = make(self.state, tcpPorts=[])
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=False), \
             mock.patch.object(cc, "tcp_probe") as tcp:
            collector.get_data()
        tcp.assert_not_called()

    def test_arp_transport_detail(self):
        with mock.patch.object(
            cc, "arp_probe", return_value=(True, "aa:bb:cc:dd:ee:ff")
        ), mock.patch.object(cc, "tcp_probe") as tcp:
            entry = make(self.state).get_data()
        tcp.assert_not_called()
        self.assertEqual(entry["transport"]["active"], "arp")
        self.assertFalse(entry["transport"]["degraded"])
        self.assertTrue(entry["transport"]["online"])
        self.assertEqual(entry["transport"]["detail"], "arp")

    def test_ping_transport_detail(self):
        with mock.patch.object(cc, "arp_probe", return_value=(False, None)), \
             mock.patch.object(cc, "ping_probe", return_value=True):
            entry = make(self.state).get_data()
        self.assertEqual(entry["transport"]["active"], "ping")
        self.assertEqual(entry["transport"]["detail"], "ping")


class ConcurrencyTests(unittest.TestCase):
    """Two collectors probing in parallel must not lose each other's state."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = Path(tmp.name) / "console_state.json"

    def test_parallel_collectors_keep_both_state_records(self):
        real_load_state = cc.load_state

        def slow_load_state(path):
            # Read first, then widen the race window: without _STATE_LOCK both
            # threads read the same empty snapshot before either saves.
            state = real_load_state(path)
            time.sleep(0.2)
            return state

        ps5 = make(self.state)
        xbox = make(self.state, id="xbox", name="Xbox Series X",
                    type="xbox", ip="192.0.2.11")
        barrier = threading.Barrier(2)
        results: dict[str, dict] = {}
        errors: list[BaseException] = []

        def run(name: str, collector: cc.ConsoleCollector) -> None:
            try:
                barrier.wait(timeout=5)
                results[name] = collector.get_data()
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        with mock.patch.object(cc, "arp_probe", return_value=(True, None)), \
             mock.patch.object(cc, "load_state", side_effect=slow_load_state):
            threads = [
                threading.Thread(target=run, args=("ps5", ps5)),
                threading.Thread(target=run, args=("xbox", xbox)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(set(results), {"ps5", "xbox"})
        self.assertTrue(all(entry["console"]["online"] for entry in results.values()))
        state = read_state(self.state)
        self.assertEqual(set(state), {"ps5", "xbox"})
        self.assertEqual(state["ps5"]["failStreak"], 0)
        self.assertEqual(state["xbox"]["failStreak"], 0)


class PingProbeTests(unittest.TestCase):
    def test_ping_probe_parses_completed_process(self):
        ok = mock.Mock(returncode=0, stdout="Pinging 1.2.3.4 with 32 bytes of data:\nttl=64")
        with mock.patch.object(lp.subprocess, "run", return_value=ok) as run:
            self.assertTrue(lp.ping_probe("1.2.3.4"))
        args = run.call_args.args[0]
        self.assertEqual(args[:3], ["ping", "-n", "1"])
        self.assertEqual(args[3:5], ["-w", "1000"])

    def test_ping_probe_timeout_is_false(self):
        import subprocess as sp
        with mock.patch.object(lp.subprocess, "run",
                               side_effect=sp.TimeoutExpired("ping", 2)):
            self.assertFalse(lp.ping_probe("1.2.3.4"))


@unittest.skipUnless(os.name == "nt", "Windows only")
class ArpSmokeTests(unittest.TestCase):
    def test_arp_probe_loopback_returns_typed_result_without_raising(self):
        reached, mac = cc.arp_probe("127.0.0.1")
        self.assertIsInstance(reached, bool)
        self.assertTrue(mac is None or isinstance(mac, str))


if __name__ == "__main__":
    unittest.main()
