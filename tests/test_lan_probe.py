"""Offline tests for LAN probes and the shared transport block."""

from __future__ import annotations

import unittest
from unittest import mock

from collectors import lan_probe as lp


class MakeTransportTests(unittest.TestCase):
    def test_shape_and_defaults(self):
        transport = lp.make_transport("usb")
        self.assertEqual(
            set(transport), {"active", "degraded", "online", "lastSeenAt", "detail"}
        )
        self.assertEqual(transport["active"], "usb")
        self.assertFalse(transport["degraded"])
        self.assertTrue(transport["online"])
        self.assertIsNone(transport["lastSeenAt"])
        self.assertIsNone(transport["detail"])

    def test_all_fields(self):
        transport = lp.make_transport(
            "lan",
            degraded=True,
            online=False,
            last_seen=1793458293,
            detail="arp",
        )
        self.assertEqual(
            transport,
            {
                "active": "lan",
                "degraded": True,
                "online": False,
                "lastSeenAt": 1793458293,
                "detail": "arp",
            },
        )

    def test_coerces_types_and_is_json_safe(self):
        import json

        transport = lp.make_transport("tcp", last_seen=1000.9, detail=9295)
        self.assertEqual(transport["lastSeenAt"], 1000)
        self.assertEqual(transport["detail"], "9295")
        json.dumps(transport)


class TcpProbeTests(unittest.TestCase):
    def test_first_open_port_wins(self):
        connection = mock.MagicMock()
        with mock.patch.object(
            lp.socket, "create_connection", return_value=connection
        ) as create:
            hit, port = lp.tcp_probe("192.0.2.10", (9295, 3074, 80))
        self.assertTrue(hit)
        self.assertEqual(port, 9295)
        create.assert_called_once_with(("192.0.2.10", 9295), timeout=0.3)
        connection.__exit__.assert_called_once()

    def test_refused_port_falls_through_to_next(self):
        connection = mock.MagicMock()
        with mock.patch.object(
            lp.socket,
            "create_connection",
            side_effect=[ConnectionRefusedError(), connection],
        ) as create:
            hit, port = lp.tcp_probe("192.0.2.10", (9295, 3074))
        self.assertTrue(hit)
        self.assertEqual(port, 3074)
        self.assertEqual(create.call_count, 2)

    def test_timeout_on_every_port_is_offline(self):
        with mock.patch.object(
            lp.socket, "create_connection", side_effect=TimeoutError()
        ):
            self.assertEqual(lp.tcp_probe("192.0.2.10", (80, 443)), (False, None))

    def test_no_ports_does_not_touch_the_network(self):
        with mock.patch.object(lp.socket, "create_connection") as create:
            self.assertEqual(lp.tcp_probe("192.0.2.10", ()), (False, None))
        create.assert_not_called()

    def test_custom_timeout_is_forwarded(self):
        connection = mock.MagicMock()
        with mock.patch.object(
            lp.socket, "create_connection", return_value=connection
        ) as create:
            lp.tcp_probe("192.0.2.10", (80,), timeout=0.05)
        create.assert_called_once_with(("192.0.2.10", 80), timeout=0.05)


class ProbeLanTests(unittest.TestCase):
    def test_arp_hit_returns_mac(self):
        with mock.patch.object(
            lp, "arp_probe", return_value=(True, "aa:bb:cc:dd:ee:ff")
        ), mock.patch.object(lp, "ping_probe") as ping:
            self.assertEqual(
                lp.probe_lan("192.0.2.10"), (True, "arp", "aa:bb:cc:dd:ee:ff")
            )
        ping.assert_not_called()

    def test_arp_miss_uses_ping(self):
        with mock.patch.object(lp, "arp_probe", return_value=(False, None)), \
             mock.patch.object(lp, "ping_probe", return_value=True):
            self.assertEqual(lp.probe_lan("192.0.2.10"), (True, "ping", None))

    def test_both_miss_reports_lan(self):
        with mock.patch.object(lp, "arp_probe", return_value=(False, None)), \
             mock.patch.object(lp, "ping_probe", return_value=False):
            self.assertEqual(lp.probe_lan("192.0.2.10"), (False, "lan", None))


class ConfigHelperTests(unittest.TestCase):
    def test_as_bool(self):
        self.assertIs(lp.as_bool(True, False), True)
        self.assertIs(lp.as_bool(False, True), False)
        self.assertIs(lp.as_bool("false", True), False)
        self.assertIs(lp.as_bool("no", True), False)
        self.assertIs(lp.as_bool("TRUE", False), True)
        self.assertIs(lp.as_bool(None, True), True)
        self.assertIs(lp.as_bool("garbage", False), False)
        self.assertIs(lp.as_bool(1, False), True)

    def test_sanitize_ports_defaults(self):
        self.assertEqual(lp.sanitize_ports(None), (9295, 3074, 80, 443))
        self.assertEqual(lp.sanitize_ports("nope"), (9295, 3074, 80, 443))

    def test_sanitize_ports_cleans_entries(self):
        self.assertEqual(
            lp.sanitize_ports([0, 70000, "443", True, 9295, 9295, -1]),
            (443, 9295),
        )

    def test_sanitize_ports_caps_at_eight(self):
        ports = lp.sanitize_ports(list(range(1000, 1020)))
        self.assertEqual(len(ports), 8)
        self.assertEqual(ports, tuple(range(1000, 1008)))

    def test_empty_list_disables_tcp(self):
        self.assertEqual(lp.sanitize_ports([]), ())


if __name__ == "__main__":
    unittest.main()
