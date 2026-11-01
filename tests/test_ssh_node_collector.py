"""Offline unittest fixtures for the C2b SSH node collector."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from collectors import ssh_node_collector as ssh
from collectors.base import CollectorError

FIXTURES = Path(__file__).resolve().parent / "fixtures"

LINUX_LAPTOP = {
    "id": "think", "name": "ThinkPad", "platform": "linux",
    "user": "u", "host": "10.0.0.2",
}
MAC_NODE = {
    "id": "mb", "name": "MacBook", "platform": "macos",
    "user": "u", "host": "10.0.0.3",
}


def completed(stdout: str = "", stderr: str = "", rc: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["ssh"], rc, stdout, stderr)


class ParseTests(unittest.TestCase):
    def test_key_value_lines(self):
        values = ssh.SshNodeCollector.parse_output(
            "battery=64\ncharging=Discharging\nmodel=ThinkPad X1\nhostname=w\n"
        )
        self.assertEqual(values["battery"], "64")
        self.assertEqual(values["charging"], "Discharging")

    def test_garbage_and_empty_lines_skipped(self):
        values = ssh.SshNodeCollector.parse_output("Welcome\n\nbattery=abc\n=odd\n")
        self.assertEqual(values, {"battery": "abc"})
        self.assertNotIn("Welcome", values)
        self.assertEqual(values.get("battery"), "abc")

    def test_value_may_contain_equals(self):
        values = ssh.SshNodeCollector.parse_output("model=A=B C\n")
        self.assertEqual(values["model"], "A=B C")


class ChargingParsingTests(unittest.TestCase):
    def test_all_real_pmset_variants(self):
        cases = (
            ("charging", True),
            ("Charging", True),
            ("finishing charge", True),
            ("Finishing Charge", True),
            ("charged", True),
            ("full", True),
            ("discharging", False),
            ("Discharging", False),
            ("AC attached; not charging", None),
            ("AC attached", None),
            ("not charging", None),
            ("", None),
            (None, None),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertIs(ssh._parse_charging(value), expected)

    def test_not_charging_is_not_mistaken_for_charging(self):
        # "discharging" and "not charging" both contain the substring "charging".
        self.assertIs(ssh._parse_charging("AC attached; not charging"), None)
        self.assertIs(ssh._parse_charging("discharging"), False)

    def test_macos_ac_attached_fixture(self):
        collector = ssh.SshNodeCollector(MAC_NODE, now=1700003600)
        stdout = (FIXTURES / "ssh_macos_ac_attached.txt").read_text(encoding="utf-8")
        with mock.patch.object(collector, "_run", return_value=completed(stdout)):
            entry = collector.get_data()
        self.assertIsNone(entry["isCharging"])
        self.assertEqual(entry["batteryLevel"], 100)


class CommandTests(unittest.TestCase):
    def test_base_command_flags(self):
        command = ssh.SshNodeCollector(LINUX_LAPTOP).build_command()
        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertIn("ConnectTimeout=2", command)
        self.assertIn("u@10.0.0.2", command)
        self.assertNotIn("-i", command)
        self.assertNotIn("IdentitiesOnly=yes", command)

    def test_ssh_key_appended_with_identities_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key = Path(tmp.name) / "id_lan"
        key.write_text("placeholder\n", encoding="utf-8")
        entry = dict(MAC_NODE, sshKey=str(key))
        command = ssh.SshNodeCollector(entry).build_command()
        index = command.index("-i")
        self.assertEqual(command[index + 1], str(key))
        self.assertIn("IdentitiesOnly=yes", command)
        self.assertLess(
            command.index("IdentitiesOnly=yes"),
            command.index("u@10.0.0.3"),
        )

    def test_ssh_key_expands_environment_variables(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key = Path(tmp.name) / "id_lan"
        key.write_text("placeholder\n", encoding="utf-8")
        entry = dict(MAC_NODE, sshKey="$STATUSCAST_TEST_KEY")
        with mock.patch.dict(os.environ, {"STATUSCAST_TEST_KEY": str(key)}):
            collector = ssh.SshNodeCollector(entry)
            command = collector.build_command()
        self.assertEqual(collector.ssh_key, str(key))
        self.assertIn(str(key), command)

    def test_ssh_key_expands_user_home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        key = Path(tmp.name) / "id_lan"
        key.write_text("placeholder\n", encoding="utf-8")
        entry = dict(MAC_NODE, sshKey="~/.ssh/id_lan")
        with mock.patch.object(ssh.os.path, "expanduser", return_value=str(key)) as expand:
            collector = ssh.SshNodeCollector(entry)
        expand.assert_called_once_with("~/.ssh/id_lan")
        self.assertEqual(collector.ssh_key, str(key))

    def test_missing_ssh_key_raises_without_leaking_material(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        missing = Path(tmp.name) / "does-not-exist"
        entry = dict(MAC_NODE, sshKey=str(missing))
        with self.assertRaises(CollectorError) as ctx:
            ssh.SshNodeCollector(entry)
        message = str(ctx.exception)
        self.assertIn("SSH 私钥不存在", message)
        self.assertIn(str(missing), message)

    def test_linux_script_has_uptime_and_first_battery(self):
        self.assertIn("/proc/uptime", ssh.LINUX_SCRIPT)
        self.assertIn(
            "cat /sys/class/power_supply/BAT*/capacity 2>/dev/null | head -1",
            ssh.LINUX_SCRIPT,
        )
        self.assertIn(
            "cat /sys/class/power_supply/BAT*/status 2>/dev/null | head -1",
            ssh.LINUX_SCRIPT,
        )

    def test_macos_script_has_boottime(self):
        self.assertIn("kern.boottime", ssh.MACOS_SCRIPT)
        self.assertIn("boottime=", ssh.MACOS_SCRIPT)

    def test_config_validation(self):
        for bad in (
            {"id": "x", "platform": "linux", "host": "h"},
            {"id": "x", "user": "u", "platform": "linux"},
            {"user": "u", "host": "h", "platform": "linux"},
            {"id": "x", "user": "u", "host": "h", "platform": "windows"},
            {"id": "x", "user": "u", "host": "h"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(CollectorError):
                    ssh.SshNodeCollector(bad)


class CollectTests(unittest.TestCase):
    def probe(self, entry, stdout="", stderr="", rc=0, now=2000):
        collector = ssh.SshNodeCollector(entry, now=now)
        with mock.patch.object(
            collector, "_run", return_value=completed(stdout, stderr, rc)
        ):
            return collector.get_data()

    def fixture(self, name: str) -> str:
        return (FIXTURES / name).read_text(encoding="utf-8")

    def test_linux_laptop_fixture_maps_standard_fields(self):
        entry = self.probe(LINUX_LAPTOP, self.fixture("ssh_linux_laptop.txt"))
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["platform"], "linux")
        self.assertEqual(entry["batteryLevel"], 64)
        self.assertIs(entry["isCharging"], False)
        self.assertEqual(entry["model"], "ThinkPad X1 Carbon Gen 11")
        self.assertEqual(entry["node"]["source"], "ssh")
        self.assertEqual(entry["node"]["uptimeSeconds"], 98765)

    def test_linux_desktop_has_no_battery_but_is_not_an_error(self):
        entry = self.probe(LINUX_LAPTOP, self.fixture("ssh_linux_desktop.txt"))
        self.assertIsNone(entry["error"])
        self.assertIsNone(entry["batteryLevel"])
        self.assertIsNone(entry["isCharging"])
        self.assertEqual(entry["model"], "MINSTOR N60")
        self.assertEqual(entry["node"]["uptimeSeconds"], 4321)

    def test_linux_multibattery_uses_first_battery(self):
        entry = self.probe(LINUX_LAPTOP, self.fixture("ssh_linux_multibattery.txt"))
        self.assertEqual(entry["batteryLevel"], 45)
        self.assertIs(entry["isCharging"], True)
        self.assertEqual(entry["model"], "Framework Laptop 13")

    def test_macos_fixture(self):
        entry = self.probe(
            MAC_NODE, self.fixture("ssh_macos.txt"), now=1700003600
        )
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["platform"], "macos")
        self.assertEqual(entry["batteryLevel"], 87)
        self.assertIs(entry["isCharging"], True)
        self.assertEqual(entry["model"], "Mac15,3")
        self.assertEqual(entry["node"]["lastSeenAt"], 1700003600)
        self.assertEqual(entry["node"]["uptimeSeconds"], 3600)

    def test_macos_boottime_in_the_future_clamps_to_zero(self):
        stdout = "battery=50\ncharging=discharging\nmodel=Mac15,3\nhostname=m\nboottime=9000\n"
        entry = self.probe(MAC_NODE, stdout, now=2000)
        self.assertEqual(entry["node"]["uptimeSeconds"], 0)

    def test_missing_uptime_is_unknown_not_an_error(self):
        entry = self.probe(LINUX_LAPTOP, "battery=10\nmodel=X\nhostname=h\n")
        self.assertIsNone(entry["node"]["uptimeSeconds"])
        self.assertIsNone(entry["error"])

    def test_node_payload_shape(self):
        entry = self.probe(LINUX_LAPTOP, self.fixture("ssh_linux_laptop.txt"))
        self.assertEqual(
            set(entry["node"]),
            {"ssh", "method", "source", "hostname", "lastSeenAt", "uptimeSeconds"},
        )

    def test_timeout_becomes_collector_error(self):
        collector = ssh.SshNodeCollector(dict(LINUX_LAPTOP, fallback=False))
        with mock.patch.object(
            collector, "_run", side_effect=subprocess.TimeoutExpired("ssh", 8)
        ):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        self.assertIn("超时", str(ctx.exception))

    def test_missing_ssh_binary(self):
        collector = ssh.SshNodeCollector(dict(LINUX_LAPTOP, fallback=False))
        with mock.patch.object(collector, "_run", side_effect=FileNotFoundError()):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        self.assertIn("OpenSSH", str(ctx.exception))

    def test_auth_failure_message_has_no_credentials(self):
        collector = ssh.SshNodeCollector(dict(LINUX_LAPTOP, fallback=False))
        stderr = "Permission denied (publickey,gssapi-keyex,gssapi-with-mic,password).\r\n"
        with mock.patch.object(collector, "_run", return_value=completed("", stderr, 255)):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        message = str(ctx.exception)
        self.assertIn("Permission denied", message)
        self.assertNotIn("u@", message)
        self.assertLessEqual(len(message), 200)

    def test_long_stderr_is_truncated(self):
        collector = ssh.SshNodeCollector(dict(LINUX_LAPTOP, fallback=False))
        with mock.patch.object(
            collector, "_run", return_value=completed("", "x" * 5000, 255)
        ):
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        self.assertLessEqual(len(str(ctx.exception)), 200)


class LanFallbackTests(unittest.TestCase):
    def failing(self, entry=None, now=5000):
        collector = ssh.SshNodeCollector(entry or LINUX_LAPTOP, now=now)
        collector._run = mock.Mock(
            side_effect=subprocess.TimeoutExpired("ssh", 8)
        )
        return collector

    def test_ssh_failure_with_lan_online_is_degraded_not_error(self):
        collector = self.failing()
        with mock.patch.object(
            ssh.socket, "gethostbyname", return_value="10.0.0.2"
        ) as resolve, mock.patch.object(
            ssh, "probe_lan", return_value=(True, "arp", "aa:bb")
        ) as probe:
            entry = collector.get_data()
        resolve.assert_called_once_with("10.0.0.2")
        probe.assert_called_once_with("10.0.0.2")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["id"], "think")
        self.assertEqual(entry["platform"], "linux")
        self.assertEqual(entry["model"], "ThinkPad")
        self.assertIsNone(entry["batteryLevel"])
        self.assertIsNone(entry["isCharging"])
        self.assertEqual(
            entry["node"],
            {
                "ssh": False,
                "online": True,
                "method": "lan",
                "source": "lan",
                "hostname": None,
                "statusText": None,
                "lastSeenAt": 5000,
                "uptimeSeconds": None,
            },
        )
        self.assertEqual(
            entry["transport"],
            {
                "active": "lan",
                "degraded": True,
                "online": True,
                "lastSeenAt": 5000,
                "detail": "arp",
            },
        )

    def test_ssh_failure_with_lan_offline_reports_offline(self):
        collector = self.failing()
        with mock.patch.object(
            ssh.socket, "gethostbyname", return_value="10.0.0.2"
        ), mock.patch.object(
            ssh, "probe_lan", return_value=(False, "lan", None)
        ):
            entry = collector.get_data()
        self.assertIsNone(entry["error"])
        self.assertFalse(entry["node"]["ssh"])
        self.assertFalse(entry["node"]["online"])
        self.assertEqual(entry["node"]["statusText"], "离线")
        self.assertIsNone(entry["node"]["lastSeenAt"])
        self.assertEqual(entry["transport"]["active"], "lan")
        self.assertFalse(entry["transport"]["online"])
        self.assertIsNone(entry["transport"]["lastSeenAt"])

    def test_model_hint_is_kept_in_degraded_entry(self):
        collector = self.failing(dict(MAC_NODE, model="MacBook Pro"))
        with mock.patch.object(
            ssh.socket, "gethostbyname", return_value="10.0.0.3"
        ), mock.patch.object(ssh, "probe_lan", return_value=(True, "ping", None)):
            entry = collector.get_data()
        self.assertEqual(entry["model"], "MacBook Pro")

    def test_fallback_false_raises_without_resolving(self):
        collector = self.failing(dict(LINUX_LAPTOP, fallback=False))
        with mock.patch.object(ssh.socket, "gethostbyname") as resolve, \
             mock.patch.object(ssh, "probe_lan") as probe:
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        resolve.assert_not_called()
        probe.assert_not_called()
        self.assertIn("超时", str(ctx.exception))

    def test_unresolvable_host_reraises_original_error(self):
        collector = self.failing()
        with mock.patch.object(
            ssh.socket, "gethostbyname", side_effect=OSError("no dns")
        ), mock.patch.object(ssh, "probe_lan") as probe:
            with self.assertRaises(CollectorError) as ctx:
                collector.get_data()
        probe.assert_not_called()
        self.assertEqual(str(ctx.exception), "ssh 连接失败：8 秒超时")

    def test_successful_ssh_entry_has_ssh_transport(self):
        collector = ssh.SshNodeCollector(LINUX_LAPTOP, now=2000)
        stdout = (FIXTURES / "ssh_linux_laptop.txt").read_text(encoding="utf-8")
        with mock.patch.object(collector, "_run", return_value=completed(stdout)):
            entry = collector.get_data()
        self.assertEqual(entry["transport"]["active"], "ssh")
        self.assertFalse(entry["transport"]["degraded"])
        self.assertTrue(entry["transport"]["online"])
        self.assertEqual(entry["transport"]["lastSeenAt"], 2000)
        self.assertEqual(entry["transport"]["detail"], "ssh")


if __name__ == "__main__":
    unittest.main()
