#!/usr/bin/env python3
"""
Tests for the web/API layer. No hardware needed -- the service is faked.

    python3 -m unittest test_webapp -v

Runs on Python 3.9 (the inverter Pi) and newer.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest

import serve
from webapp import safety
from webapp.app import create_app
from webapp.safety import CommandRejected
from webapp.service import parse_qpigs, parse_qpiri
from transport import InverterError

SAMPLE_QPIGS = ("(232.0 49.9 231.0 50.0 0000 0001 000 401 24.20 000 050 0026 "
                "0000 000.0 24.20 00000 00010110 00 00 00000 010")
SAMPLE_QPIRI = ("(230.0 26.1 230.0 50.0 26.1 6000 6000 24.0 11.0 10.5 14.1 13.5 "
                "2 60 06P 0 0 1 0 1 6 01 52.0 0 0")

TOKEN = "test-token-123"


class FakeService:
    """Stands in for InverterService without touching a serial port."""

    def __init__(self, battery_voltage=26.0, allow_writes=True,
                 min_battery_voltage=24.0, ack=True):
        self.allow_writes = allow_writes
        self.min_battery_voltage = min_battery_voltage
        self.poll_interval = 10.0
        self._battery_voltage = battery_voltage
        self._ack = ack
        self.sent = []

    def battery_voltage(self):
        return self._battery_voltage

    def query(self, command):
        if command == "QPIGS":
            return SAMPLE_QPIGS
        if command == "QPIRI":
            return SAMPLE_QPIRI
        if command == "QMOD":
            return "(L"
        return "(PI30"

    def send_set(self, command):
        self.sent.append(command)
        return "(ACK" if self._ack else "(NAK"

    def latest(self):
        return {"status": parse_qpigs(SAMPLE_QPIGS), "error": None,
                "connected": True, "last_success": "2026-01-01T00:00:00+00:00",
                "poll_interval": self.poll_interval,
                "allow_writes": self.allow_writes,
                "min_battery_voltage": self.min_battery_voltage}

    def history(self):
        return []

    def audit_log(self):
        return []

    def device_info(self, refresh=False):
        return {"protocol": "PI30", "port": "/dev/null"}

    def ratings(self, refresh=False):
        return parse_qpiri(SAMPLE_QPIRI)

    def refresh(self):
        return parse_qpigs(SAMPLE_QPIGS)


def client_for(service):
    app = create_app(service, token=TOKEN, secret_key="unit-test-secret")
    app.config["TESTING"] = True
    return app.test_client()


AUTH = {"X-API-Key": TOKEN}


def post(client, path, payload, **headers):
    hdrs = dict(AUTH)
    hdrs.update(headers)
    return client.post(path, data=json.dumps(payload),
                       content_type="application/json", headers=hdrs)


class TestParsers(unittest.TestCase):
    def test_qpigs_types_and_flags(self):
        s = parse_qpigs(SAMPLE_QPIGS)
        self.assertEqual(s["battery_voltage"], 24.2)
        self.assertEqual(s["battery_capacity"], 50)
        self.assertIsInstance(s["ac_output_active_power"], int)
        self.assertTrue(s["status_flags"]["b3_load_on"])
        self.assertEqual(s["battery_net_current"], 0)

    def test_qpigs_rejects_truncated_frame(self):
        # The 2400-baud link really does return short frames; they must not
        # be parsed into bogus values.
        with self.assertRaises(InverterError):
            parse_qpigs("(232.0 49.9 231.0")

    def test_qpiri_scales_12v_block_setpoints(self):
        r = parse_qpiri(SAMPLE_QPIRI)
        # 13.5 V per 12V block on a 24V pack -> 27.0 V actual
        self.assertEqual(r["battery_float_voltage"]["actual_volts"], 27.0)
        self.assertEqual(r["battery_under_voltage"]["actual_volts"], 21.0)
        self.assertEqual(r["battery_type"]["label"], "User-defined")

    def test_qpiri_keeps_malformed_field_raw(self):
        # max_charging_current reads "06P" on this unit -- keep it visible
        # rather than silently dropping it.
        r = parse_qpiri(SAMPLE_QPIRI)
        self.assertEqual(r["max_charging_current"]["value"], "06P")


class TestPolicy(unittest.TestCase):
    def test_query_is_not_a_write(self):
        self.assertEqual(safety.classify("QPIGS")["kind"], "query")

    def test_bare_id_resolves_to_the_query(self):
        # "ID" appears in both tables; read-only must win.
        self.assertEqual(safety.classify("ID")["kind"], "query")

    def test_unknown_command_rejected(self):
        with self.assertRaises(CommandRejected) as cm:
            safety.check_policy("NOPE", confirm=False, allow_writes=True)
        self.assertEqual(cm.exception.code, "unknown_command")

    def test_read_only_server_refuses_writes(self):
        with self.assertRaises(CommandRejected) as cm:
            safety.check_policy("PCP01", confirm=False, allow_writes=False)
        self.assertEqual(cm.exception.code, "read_only")

    def test_dangerous_needs_confirmation(self):
        with self.assertRaises(CommandRejected) as cm:
            safety.check_policy("REEP", confirm=False, allow_writes=True)
        self.assertEqual(cm.exception.code, "confirmation_required")
        # ...and goes through once confirmed
        info = safety.check_policy("REEP", confirm=True, allow_writes=True)
        self.assertEqual(info["prefix"], "REEP")

    def test_solar_only_blocked_below_floor(self):
        with self.assertRaises(CommandRejected) as cm:
            safety.check_policy("PCP03", confirm=False, allow_writes=True,
                                battery_voltage=23.4, min_battery_voltage=24.0)
        self.assertEqual(cm.exception.code, "battery_too_low")

    def test_solar_only_allowed_above_floor(self):
        info = safety.check_policy("PCP03", confirm=False, allow_writes=True,
                                   battery_voltage=26.5, min_battery_voltage=24.0)
        self.assertEqual(info["command"], "PCP03")

    def test_solar_only_blocked_when_voltage_unknown(self):
        # Unknown voltage must fail closed, not open.
        with self.assertRaises(CommandRejected) as cm:
            safety.check_policy("PCP03", confirm=False, allow_writes=True,
                                battery_voltage=None, min_battery_voltage=24.0)
        self.assertEqual(cm.exception.code, "battery_unknown")

    def test_other_pcp_values_unaffected_by_floor(self):
        for value in ("00", "01", "02"):
            info = safety.check_policy("PCP" + value, confirm=False, allow_writes=True,
                                       battery_voltage=20.0, min_battery_voltage=24.0)
            self.assertEqual(info["command"], "PCP" + value)


class TestApi(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.client = client_for(self.service)

    def test_health_is_public(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)

    def test_api_requires_token(self):
        self.assertEqual(self.client.get("/api/status").status_code, 401)

    def test_wrong_token_rejected(self):
        r = self.client.get("/api/status", headers={"X-API-Key": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_bearer_token_accepted(self):
        r = self.client.get("/api/status",
                            headers={"Authorization": f"Bearer {TOKEN}"})
        self.assertEqual(r.status_code, 200)

    def test_status_payload(self):
        body = self.client.get("/api/status", headers=AUTH).get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["status"]["battery_voltage"], 24.2)

    def test_writes_require_json_content_type(self):
        r = self.client.post("/api/command", data="command=SOFF", headers=AUTH)
        self.assertEqual(r.status_code, 415)

    def test_charger_priority_write(self):
        r = post(self.client, "/api/charger-priority", {"value": "01"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(self.service.sent, ["PCP01"])

    def test_charger_priority_rejects_bad_value(self):
        r = post(self.client, "/api/charger-priority", {"value": "07"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.service.sent, [])

    def test_solar_only_blocked_over_http(self):
        svc = FakeService(battery_voltage=22.0, min_battery_voltage=24.0)
        r = post(client_for(svc), "/api/charger-priority", {"value": "03"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "battery_too_low")
        self.assertEqual(svc.sent, [])   # nothing reached the inverter

    def test_read_only_server_over_http(self):
        svc = FakeService(allow_writes=False)
        r = post(client_for(svc), "/api/command", {"command": "PCP01"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(svc.sent, [])

    def test_query_via_command_endpoint(self):
        r = post(self.client, "/api/command", {"command": "QPIGS"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["kind"], "query")
        self.assertEqual(self.service.sent, [])

    def test_nak_reports_failure(self):
        svc = FakeService(ack=False)
        r = post(client_for(svc), "/api/command", {"command": "PCP01"})
        self.assertEqual(r.status_code, 502)
        self.assertFalse(r.get_json()["ok"])

    def test_login_with_token_grants_session(self):
        client = client_for(FakeService())
        r = client.post("/login", data={"token": TOKEN})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(client.get("/api/status").status_code, 200)

    def test_login_rejects_wrong_token(self):
        client = client_for(FakeService())
        r = client.post("/login", data={"token": "nope"})
        self.assertEqual(r.status_code, 200)      # re-renders with an error
        self.assertEqual(client.get("/api/status").status_code, 401)

    def test_login_ignores_offsite_redirect(self):
        client = client_for(FakeService())
        r = client.post("/login?next=https://evil.example/x", data={"token": TOKEN})
        self.assertNotIn("evil.example", r.headers.get("Location", ""))

    def test_dashboard_redirects_when_unauthenticated(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_dashboard_renders_when_authenticated(self):
        client = client_for(FakeService())
        client.post("/login", data={"token": TOKEN})
        r = client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Live status", r.data)


class TestConfigDurability(unittest.TestCase):
    """The Pi this runs on reboots uncleanly; the config must survive it."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "web.json")

    def test_first_run_mints_credentials(self):
        cfg = serve.load_config(self.path)
        self.assertTrue(cfg["token"])
        self.assertTrue(cfg["secret_key"])
        self.assertTrue(os.path.exists(self.path))

    def test_token_is_stable_across_loads(self):
        first = serve.load_config(self.path)["token"]
        self.assertEqual(serve.load_config(self.path)["token"], first)

    def test_config_is_owner_only(self):
        serve.load_config(self.path)
        mode = stat.S_IMODE(os.stat(self.path).st_mode)
        self.assertEqual(mode, 0o600)

    def test_zero_length_config_is_regenerated(self):
        # Exactly what an unclean reboot left behind: the inode survived,
        # the data did not.
        open(self.path, "w").close()
        cfg = serve.load_config(self.path)
        self.assertTrue(cfg["token"])
        self.assertGreater(os.path.getsize(self.path), 0)

    def test_corrupt_config_refuses_rather_than_rotating_the_token(self):
        # Regenerating here would silently lock out every existing client.
        with open(self.path, "w") as fh:
            fh.write("{not json at all")
        with self.assertRaises(serve.ConfigError):
            serve.load_config(self.path)

    def test_save_leaves_no_temp_file_behind(self):
        serve.load_config(self.path)
        self.assertEqual(sorted(os.listdir(self.dir)), ["web.json"])

    def test_save_is_atomic_under_failure(self):
        # If serialisation blows up mid-write, the existing config must be
        # left intact rather than truncated.
        cfg = serve.load_config(self.path)
        original = open(self.path).read()
        cfg["bad"] = {1, 2}          # a set is not JSON-serialisable
        with self.assertRaises(TypeError):
            serve.save_config(self.path, cfg)
        self.assertEqual(open(self.path).read(), original)
        self.assertEqual(sorted(os.listdir(self.dir)), ["web.json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
