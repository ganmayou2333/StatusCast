"""NekoTrak collector: parsing, liveness, Steam lookup and the schema-3 row.

Everything here is offline. NekoTrak data comes from ``tests/fixtures``, the
Steam library is a synthetic tree built in a temporary directory, and liveness
is driven through the injectable ``process_check`` so the suite never depends on
whether the real NekoTrak happens to be running on the machine.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import status_core
from collectors import nekotrak_collector as nc
from collectors import steam_index
from collectors.base import CollectorError

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
HOME_FIXTURE = FIXTURES / "nekotrak_home"
CONTRACT = json.loads(
    (REPO_ROOT / "schema" / "status-contract.json").read_text(encoding="utf-8")
)

CST = timezone(timedelta(hours=8))
# One minute after the fixture session reached "Ready".
NOW = datetime(2026, 10, 30, 12, 5, 0, tzinfo=CST)


def read_fixture(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text(encoding="utf-8")


def make_home(testcase: unittest.TestCase) -> Path:
    """Copy the fixture data root into a temp dir the test may mutate."""

    tmp = tempfile.TemporaryDirectory()
    testcase.addCleanup(tmp.cleanup)
    home = Path(tmp.name) / "NekoTrak"
    shutil.copytree(HOME_FIXTURE, home)
    return home


def mark_heartbeat(home: Path, moment: datetime = NOW) -> Path:
    """Write the heartbeat file and backdate it to ``moment``."""

    path = home / nc.HEARTBEAT_FILE
    path.write_text("alive", encoding="utf-8")
    stamp = moment.timestamp()
    os.utime(path, (stamp, stamp))
    return path


class NekoTrakTestCase(unittest.TestCase):
    def running_home(self) -> Path:
        home = make_home(self)
        mark_heartbeat(home)
        return home


class StartupLogTests(NekoTrakTestCase):
    def test_online_session_reports_uptime_and_version(self):
        parsed = nc.parse_startup_log(read_fixture("nekotrak_home", "startup.log"), now=NOW)
        self.assertTrue(parsed["online"])
        self.assertEqual(parsed["version"], "1.1.13")
        self.assertEqual(parsed["pid"], 222)
        self.assertEqual(parsed["uptimeSec"], 300)
        self.assertIsNotNone(parsed["startedAt"])
        self.assertEqual(parsed["warns"], [])

    def test_exited_session_is_offline(self):
        text = "\n".join(
            [
                "2026-10-30T10:00:00.000000000+08:00 [Startup] pid=1 version=1.1.13 phase=3 active_ms=1000 entered Ready",
                "2026-10-30T10:10:00.000000000+08:00 [Startup] pid=1 version=1.1.13 phase=5 active_ms=601000 exit requested",
            ]
        )
        parsed = nc.parse_startup_log(text, now=NOW)
        self.assertFalse(parsed["online"])

    def test_missing_log_is_a_warning_not_an_error(self):
        parsed = nc.parse_startup_log(None, now=NOW)
        self.assertFalse(parsed["online"])
        self.assertIsNone(parsed["version"])
        self.assertTrue(parsed["warns"])


class TrackerLogTests(NekoTrakTestCase):
    def test_stop_and_timeout_counters(self):
        parsed = nc.parse_tracker_log(
            read_fixture(
                "nekotrak_home",
                "com.bocchithedev.nekotrak",
                "logs",
                "NekoTrak.log",
            )
        )
        self.assertFalse(parsed["trackerActive"])
        self.assertEqual(parsed["customTextFailures"], 2)
        self.assertEqual(parsed["customTextElapsedMs"], [5010.0, 5012.0])
        # NekoTrak's log lines carry a bare wall clock and no offset, so the
        # parser attaches the machine's local zone. Assert the wall clock only:
        # pinning an offset here made the suite fail on UTC CI runners.
        self.assertTrue(parsed["trackerStartedAt"].startswith("2026-10-30T12:00:02"))
        self.assertTrue(parsed["trackerStoppedAt"].startswith("2026-10-30T12:03:00"))

    def test_start_after_stop_is_active(self):
        parsed = nc.parse_tracker_log(
            "\n".join(
                [
                    "[2026-10-30][10:00:00][INFO][tracker::core] [TRACKER] Sent stop signal to window monitor thread",
                    "[2026-10-30][10:05:00][INFO][tracker::core] [TRACKER] All monitors started",
                ]
            )
        )
        self.assertTrue(parsed["trackerActive"])


class PetTalesTests(NekoTrakTestCase):
    def test_last_event_wins(self):
        parsed = nc.parse_pet_tales(
            read_fixture("nekotrak_home", "users", "76561190000000000", "pet-tales.txt")
        )
        self.assertEqual(parsed["name"], "Aymer1K")
        self.assertEqual(parsed["eventCount"], 2)
        self.assertEqual(parsed["lastEvent"]["category"], "eat")
        self.assertIn("进食", parsed["lastEvent"]["summary"])
        self.assertTrue(parsed["emoji"])

    def test_garbage_degrades_to_warning(self):
        parsed = nc.parse_pet_tales("\n{bad}\n")
        self.assertIsNone(parsed["lastEvent"])
        self.assertTrue(parsed["warns"])


class SteamLookupTests(unittest.TestCase):
    """The Steam index is exercised on a synthetic library, never the real one."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "Steam"
        steamapps = self.root / "steamapps"
        (steamapps / "common" / "Cyberpunk 2077").mkdir(parents=True)
        shutil.copy(
            FIXTURES / "steam" / "steam_appmanifest_1091500.acf",
            steamapps / "appmanifest_1091500.acf",
        )
        escaped = str(self.root).replace("\\", "\\\\")
        (steamapps / "libraryfolders.vdf").write_text(
            '"libraryfolders"\n{\n\t"0"\n\t{\n\t\t"path"\t\t"' + escaped + '"\n\t}\n}\n',
            encoding="utf-8",
        )
        steam_index.reset_cache()
        self.addCleanup(steam_index.reset_cache)
        self.index = steam_index.build_index(self.root, force=True)

    def test_vdf_parser_handles_nesting(self):
        parsed = steam_index.parse_vdf(
            '"root"\n{\n\t"a" "1"\n\t"b"\n\t{\n\t\t"c" "C:\\\\Games"\n\t}\n}\n'
        )
        self.assertEqual(parsed["root"]["a"], "1")
        self.assertEqual(parsed["root"]["b"]["c"], "C:\\Games")

    def test_unbalanced_vdf_never_raises(self):
        self.assertEqual(steam_index.parse_vdf(""), {})
        self.assertIsInstance(steam_index.parse_vdf('"a" {'), dict)

    def test_match_path_resolves_the_installdir(self):
        exe = str(
            self.root
            / "steamapps"
            / "common"
            / "Cyberpunk 2077"
            / "bin"
            / "x64"
            / "Cyberpunk2077.exe"
        )
        match = self.index.match_path(exe)
        self.assertIsNotNone(match)
        self.assertEqual(match["name"], "Cyberpunk 2077")
        self.assertEqual(match["appid"], "1091500")

    def test_match_path_misses_foreign_executables(self):
        self.assertIsNone(self.index.match_path("C:\\Windows\\notepad.exe"))
        self.assertIsNone(self.index.match_path(None))


class LivenessTests(NekoTrakTestCase):
    def test_fresh_heartbeat_counts_as_running(self):
        home = self.running_home()
        self.assertTrue(nc.heartbeat_fresh(home, now=NOW))
        self.assertTrue(
            nc.is_running(home, now=NOW, process_check=lambda: False)
        )

    def test_stale_heartbeat_needs_the_process_check(self):
        home = make_home(self)
        mark_heartbeat(home, NOW - timedelta(minutes=5))
        self.assertFalse(nc.heartbeat_fresh(home, now=NOW))
        self.assertFalse(nc.is_running(home, now=NOW, process_check=lambda: False))
        self.assertTrue(nc.is_running(home, now=NOW, process_check=lambda: True))

    def test_available_reflects_the_data_root(self):
        self.assertFalse(nc.available(make_home(self) / "missing"))
        self.assertTrue(nc.available(self.running_home()))


class CollectTests(NekoTrakTestCase):
    def test_fixture_home_produces_the_expected_payload(self):
        payload = nc.collect(
            self.running_home(),
            now=NOW,
            resolve_steam=False,
            process_check=lambda: True,
        )
        self.assertEqual(payload["source"], "L1")
        self.assertEqual(payload["version"], "1.1.13")
        self.assertEqual(payload["uptimeSec"], 300)
        self.assertIsNone(payload["currentGame"])
        self.assertEqual(payload["pet"]["name"], "Aymer1K")
        self.assertFalse(payload["trackerActive"])
        self.assertIsNone(payload["pomodoro"])
        self.assertIn("前台窗口进程", " ".join(payload["warns"]))

    def test_not_running_raises_the_standard_error(self):
        # No heartbeat and no process: the app was killed without an exit record.
        with self.assertRaises(CollectorError) as caught:
            nc.collect(
                make_home(self),
                now=NOW,
                resolve_steam=False,
                process_check=lambda: False,
            )
        self.assertIn("未运行", str(caught.exception))

    def test_empty_data_root_is_not_running(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with self.assertRaises(CollectorError):
            nc.collect(
                Path(tmp.name), now=NOW, resolve_steam=False, process_check=lambda: True
            )

    def test_corrupt_json_degrades_to_a_warning(self):
        home = self.running_home()
        (home / "local.json").write_text("{ broken", encoding="utf-8")
        payload = nc.collect(
            home, now=NOW, resolve_steam=False, process_check=lambda: True
        )
        self.assertEqual(payload["version"], "1.1.13")
        self.assertTrue(any("JSON" in warn for warn in payload["warns"]))

    def test_missing_users_directory_still_collects(self):
        home = self.running_home()
        shutil.rmtree(home / "users")
        payload = nc.collect(
            home, now=NOW, resolve_steam=False, process_check=lambda: True
        )
        self.assertIsNone(payload["pet"]["name"])
        self.assertTrue(any("users" in warn for warn in payload["warns"]))


class DeviceEntryTests(NekoTrakTestCase):
    def entry(self) -> dict:
        collector = nc.NekoTrakCollector(
            home=self.running_home(),
            now=NOW,
            process_check=lambda: True,
            resolve_steam=False,
        )
        return collector.get_data()

    def test_entry_matches_the_schema_contract(self):
        entry = self.entry()
        self.assertEqual(
            set(entry),
            set(CONTRACT["collectorDeviceFields"]) | {"nekotrak", "transport"},
        )
        self.assertEqual(
            set(entry["nekotrak"]), set(CONTRACT["additiveFields"]["nekotrak"])
        )
        self.assertEqual(
            set(entry["transport"]), set(CONTRACT["additiveFields"]["transport"])
        )

    def test_identity_and_transport(self):
        entry = self.entry()
        self.assertEqual(entry["id"], "nekotrak")
        self.assertEqual(entry["platform"], "windows")
        self.assertEqual(entry["model"], "NekoTrak 1.1.13")
        self.assertIsNone(entry["error"])
        self.assertIsNone(entry["batteryLevel"])
        self.assertEqual(entry["transport"]["active"], "process")
        self.assertTrue(entry["transport"]["online"])
        self.assertFalse(entry["transport"]["degraded"])

    def test_field_order_of_nektorak_keys_matches_the_contract(self):
        # The contract lists the keys; the collector must not silently add one.
        self.assertEqual(
            tuple(CONTRACT["additiveFields"]["nekotrak"]), nc.NEKOTRAK_FIELDS
        )

    def test_failure_becomes_the_standard_error_entry(self):
        collector = nc.NekoTrakCollector(
            home=make_home(self),
            now=NOW,
            process_check=lambda: False,
            resolve_steam=False,
        )
        with self.assertRaises(CollectorError):
            collector.get_data()
        payload = status_core.run_collector(collector)
        self.assertEqual(payload["id"], "nekotrak")
        self.assertEqual(payload["platform"], "windows")
        self.assertIn("未运行", payload["error"])
        self.assertEqual(payload["transport"]["active"], "none")


class DispatcherWiringTests(unittest.TestCase):
    def test_registered_last_when_the_data_root_exists(self):
        with mock.patch.object(
            status_core, "nekotrak_available", return_value=True
        ):
            collectors, invalid = status_core.build_collectors({})
        self.assertEqual(
            [collector.device_id for collector in collectors],
            ["iphone", "android", "nekotrak"],
        )
        self.assertEqual(invalid, [])

    def test_absent_on_a_machine_that_never_ran_nekotrak(self):
        with mock.patch.object(
            status_core, "nekotrak_available", return_value=False
        ):
            collectors, _ = status_core.build_collectors({})
        self.assertEqual(
            [collector.device_id for collector in collectors], ["iphone", "android"]
        )


if __name__ == "__main__":
    unittest.main()
