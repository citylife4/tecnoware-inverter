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
from datetime import datetime, timedelta

import serve
from webapp import safety
from webapp.app import create_app
from webapp.grid_charge import GridChargeController, validate_config
from webapp.safety import CommandRejected
from webapp.scheduler import Scheduler, validate_rules
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
        self.sent_sources = []

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

    def send_set(self, command, source="manual"):
        self.sent.append(command)
        self.sent_sources.append(source)
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


def client_for(service, scheduler=None, grid_charge=None):
    app = create_app(service, scheduler, grid_charge, token=TOKEN,
                     secret_key="unit-test-secret")
    app.config["TESTING"] = True
    return app.test_client()


class FetchStub:
    """Stands in for GridChargeController's real HTTP poll of the
    auto-energy dashboard. `net_balance = None` simulates the dashboard
    being unreachable; mutate `.net_balance` between ticks to simulate the
    reading changing over time without needing a real server or sleeps."""

    def __init__(self, net_balance=None, timestamp="2026-01-01 00:00:00"):
        self.net_balance = net_balance
        self.timestamp = timestamp
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.net_balance is None:
            return None, None
        return self.net_balance, self.timestamp


def window_around_now(pad_minutes=3):
    """An HH:MM/HH:MM window guaranteed to contain "now" at the moment the
    caller's tick() actually runs, however long test setup around it takes.
    Handles midnight wraparound the same way pick_rule/rule_active do."""
    now = datetime.now()
    start = (now - timedelta(minutes=pad_minutes)).strftime("%H:%M")
    end = (now + timedelta(minutes=pad_minutes)).strftime("%H:%M")
    return start, end


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


class TestScheduleValidation(unittest.TestCase):
    def test_accepts_well_formed_rules(self):
        rules = validate_rules([{"from": "09:00", "to": "17:00", "pcp": "03", "why": "day"}])
        self.assertEqual(rules[0]["pcp"], "03")

    def test_rejects_bad_pcp(self):
        with self.assertRaises(ValueError):
            validate_rules([{"from": "09:00", "to": "17:00", "pcp": "09"}])

    def test_rejects_bad_time_format(self):
        with self.assertRaises(ValueError):
            validate_rules([{"from": "9am", "to": "17:00", "pcp": "01"}])

    def test_rejects_missing_time(self):
        with self.assertRaises(ValueError):
            validate_rules([{"to": "17:00", "pcp": "01"}])

    def test_rejects_non_list(self):
        with self.assertRaises(ValueError):
            validate_rules({"from": "09:00", "to": "17:00", "pcp": "01"})

    def test_why_is_optional_and_truncated(self):
        rules = validate_rules([{"from": "09:00", "to": "17:00", "pcp": "01",
                                 "why": "x" * 500}])
        self.assertEqual(len(rules[0]["why"]), 200)


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "web_schedule.json")

    def make(self, **kwargs):
        service = FakeService(**kwargs)
        sched = Scheduler(service, self.path, poll_interval=60.0)
        return service, sched

    def test_fresh_state_is_disabled_with_no_rules(self):
        _, sched = self.make()
        state = sched.get_state()
        self.assertFalse(state["enabled"])
        self.assertEqual(state["rules"], [])

    def test_set_state_persists_to_disk(self):
        _, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(False, [{"from": frm, "to": to, "pcp": "01", "why": "x"}])
        # A fresh Scheduler pointed at the same file must see it.
        reloaded = Scheduler(FakeService(), self.path)
        self.assertEqual(reloaded.get_state()["rules"][0]["pcp"], "01")

    def test_set_state_rejects_invalid_rules_without_touching_stored_state(self):
        service, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "01", "why": "keep me"}])
        with self.assertRaises(ValueError):
            sched.set_state(True, [{"from": "bad", "to": to, "pcp": "01"}])
        # The good rule set from before the bad call must still be there.
        self.assertEqual(sched.get_state()["rules"][0]["why"], "keep me")

    def test_matching_rule_is_applied(self):
        service, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "02", "why": "test"}])
        self.assertEqual(service.sent, ["PCP02"])
        self.assertEqual(service.sent_sources, ["scheduler"])

    def test_idempotent_does_not_resend_unchanged_target(self):
        service, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "02", "why": "t"}])
        sched.tick()
        sched.tick()
        self.assertEqual(service.sent, ["PCP02"])   # only the first tick actually sent it

    def test_force_resends_even_if_unchanged(self):
        service, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "02", "why": "t"}])
        result = sched.tick(force=True)
        self.assertTrue(result["applied"])
        self.assertEqual(service.sent, ["PCP02", "PCP02"])

    def test_disabled_scheduler_sends_nothing(self):
        service, sched = self.make()
        frm, to = window_around_now()
        sched.set_state(False, [{"from": frm, "to": to, "pcp": "02", "why": "t"}])
        result = sched.tick()
        self.assertEqual(result["note"], "scheduler disabled")
        self.assertEqual(service.sent, [])

    def test_no_matching_rule_sends_nothing(self):
        service, sched = self.make()
        # A window that (barring a midnight-crossing test run) excludes now.
        far = (datetime.now() + timedelta(hours=6)).strftime("%H:%M")
        far2 = (datetime.now() + timedelta(hours=7)).strftime("%H:%M")
        sched.set_state(True, [{"from": far, "to": far2, "pcp": "02", "why": "t"}])
        self.assertEqual(service.sent, [])

    def test_read_only_service_blocks_apply(self):
        service, sched = self.make(allow_writes=False)
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "02", "why": "t"}])
        self.assertEqual(service.sent, [])
        self.assertIn("read-only", sched.get_state()["last_run"]["note"])

    def test_solar_only_downgraded_below_floor(self):
        service, sched = self.make(battery_voltage=20.0, min_battery_voltage=24.0)
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "03", "why": "daytime"}])
        self.assertEqual(service.sent, ["PCP01"])
        self.assertIn("OVERRIDE", sched.get_state()["last_run"]["why"])

    def test_solar_only_allowed_above_floor(self):
        service, sched = self.make(battery_voltage=27.0, min_battery_voltage=24.0)
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "03", "why": "daytime"}])
        self.assertEqual(service.sent, ["PCP03"])

    def test_solar_only_downgraded_when_voltage_unknown(self):
        service, sched = self.make(battery_voltage=None, min_battery_voltage=24.0)
        frm, to = window_around_now()
        sched.set_state(True, [{"from": frm, "to": to, "pcp": "03", "why": "daytime"}])
        self.assertEqual(service.sent, ["PCP01"])

    def test_corrupt_state_file_starts_disabled_rather_than_crashing(self):
        with open(self.path, "w") as fh:
            fh.write("{not json")
        _, sched = self.make()
        self.assertFalse(sched.get_state()["enabled"])


class TestScheduleApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "web_schedule.json")

    def test_get_without_scheduler_configured(self):
        client = client_for(FakeService(), scheduler=None)
        r = client.get("/api/schedule", headers=AUTH)
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.get_json()["code"], "no_scheduler")

    def test_get_returns_current_state(self):
        sched = Scheduler(FakeService(), self.path)
        client = client_for(FakeService(), sched)
        r = client.get("/api/schedule", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.get_json()["enabled"])

    def test_put_via_put_method(self):
        service = FakeService()
        sched = Scheduler(service, self.path)
        client = client_for(service, sched)
        frm, to = window_around_now()
        r = client.put("/api/schedule", data=json.dumps(
            {"enabled": True, "rules": [{"from": frm, "to": to, "pcp": "01", "why": "t"}]}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(service.sent, ["PCP01"])

    def test_put_rejects_invalid_rules(self):
        service = FakeService()
        sched = Scheduler(service, self.path)
        client = client_for(service, sched)
        r = client.put("/api/schedule", data=json.dumps(
            {"enabled": True, "rules": [{"from": "bad", "to": "17:00", "pcp": "01"}]}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "invalid_rules")
        self.assertEqual(service.sent, [])

    def test_put_requires_auth(self):
        client = client_for(FakeService(), Scheduler(FakeService(), self.path))
        r = client.put("/api/schedule", data=json.dumps({"enabled": False, "rules": []}),
                       content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_apply_now_forces_a_tick(self):
        service = FakeService()
        sched = Scheduler(service, self.path)
        client = client_for(service, sched)
        frm, to = window_around_now()
        client.put("/api/schedule", data=json.dumps(
            {"enabled": True, "rules": [{"from": frm, "to": to, "pcp": "02", "why": "t"}]}),
            content_type="application/json", headers=AUTH)
        service.sent.clear()
        r = post(client, "/api/schedule/apply-now", {"force": True})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["result"]["applied"])
        self.assertEqual(service.sent, ["PCP02"])

    def test_read_only_server_stores_but_does_not_apply(self):
        service = FakeService(allow_writes=False)
        sched = Scheduler(service, self.path)
        client = client_for(service, sched)
        frm, to = window_around_now()
        r = client.put("/api/schedule", data=json.dumps(
            {"enabled": True, "rules": [{"from": frm, "to": to, "pcp": "02", "why": "t"}]}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["enabled"])   # stored
        self.assertEqual(service.sent, [])          # not applied


class TestGridChargeValidation(unittest.TestCase):
    def test_defaults_are_valid(self):
        cfg = validate_config({})
        self.assertFalse(cfg["enabled"])
        self.assertEqual(cfg["charge_pcp"], "01")

    def test_rejects_non_dict(self):
        with self.assertRaises(ValueError):
            validate_config([1, 2, 3])

    def test_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            validate_config({"totally_made_up": 1})

    def test_rejects_non_http_url(self):
        with self.assertRaises(ValueError):
            validate_config({"source_url": "ftp://example/live"})

    def test_rejects_inverted_thresholds(self):
        with self.assertRaises(ValueError):
            validate_config({"export_threshold_w": 10, "import_threshold_w": -10})

    def test_rejects_bad_pcp(self):
        with self.assertRaises(ValueError):
            validate_config({"charge_pcp": "07"})

    def test_rejects_too_fast_polling(self):
        with self.assertRaises(ValueError):
            validate_config({"poll_interval": 1})


class TestGridChargeController(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "web_gridcharge.json")

    def make(self, net_balance=None, **service_kwargs):
        service = FakeService(**service_kwargs)
        stub = FetchStub(net_balance)
        gc = GridChargeController(service, self.path, fetch_fn=stub)
        return service, stub, gc

    def test_fresh_state_is_disabled(self):
        _, _, gc = self.make()
        self.assertFalse(gc.get_state()["enabled"])

    def test_exporting_enables_charging(self):
        service, _, gc = self.make(net_balance=-100)
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, ["PCP01"])
        self.assertEqual(service.sent_sources, ["grid_export"])

    def test_importing_keeps_idle(self):
        service, _, gc = self.make(net_balance=100)
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, ["PCP03"])

    def test_deadband_holds_previous_state(self):
        service, stub, gc = self.make(net_balance=-100)   # exporting
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, ["PCP01"])
        stub.net_balance = -10   # inside the -50..20 dead-band
        gc.tick()
        # Desired state carries over ("charging"), so target is unchanged
        # and nothing new is sent -- this is the hysteresis, not a bug.
        self.assertEqual(service.sent, ["PCP01"])

    def test_low_battery_overrides_import_state(self):
        service, _, gc = self.make(net_balance=100,   # importing -> would be idle
                                   battery_voltage=20.0, min_battery_voltage=24.0)
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, ["PCP01"])
        self.assertIn("OVERRIDE", gc.get_state()["last_run"]["why"])

    def test_unreachable_dashboard_falls_back_to_idle(self):
        service, _, gc = self.make(net_balance=None)   # fetch always fails
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, ["PCP03"])

    def test_stale_reading_falls_back_to_idle(self):
        service, stub, gc = self.make(net_balance=-100)
        gc.set_config({"enabled": True, "min_switch_interval": 0, "stale_after": 0})
        self.assertEqual(service.sent, ["PCP01"])
        stub.net_balance = None   # dashboard now unreachable
        gc.tick()
        self.assertEqual(service.sent, ["PCP01", "PCP03"])

    def test_read_only_service_blocks_apply(self):
        service, _, gc = self.make(net_balance=-100, allow_writes=False)
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(service.sent, [])
        self.assertIn("read-only", gc.get_state()["last_run"]["note"])

    def test_config_persists_to_disk(self):
        _, _, gc = self.make()
        gc.set_config({"export_threshold_w": -77})
        reloaded = GridChargeController(FakeService(), self.path, fetch_fn=FetchStub())
        self.assertEqual(reloaded.get_state()["export_threshold_w"], -77)

    def test_set_enabled_preserves_other_settings(self):
        service, stub, gc = self.make(net_balance=-100)
        gc.set_config({"export_threshold_w": -77, "min_switch_interval": 0})
        stub.net_balance = -80   # below the custom -77 threshold
        gc.set_enabled(True)
        self.assertEqual(gc.get_state()["export_threshold_w"], -77)
        self.assertEqual(service.sent, ["PCP01"])

    def test_force_bypasses_dwell_time(self):
        service, stub, gc = self.make(net_balance=-100)
        gc.set_config({"enabled": True, "min_switch_interval": 99999})
        self.assertEqual(service.sent, ["PCP01"])
        stub.net_balance = 100   # now importing
        gc.tick(force=True)
        self.assertEqual(service.sent, ["PCP01", "PCP03"])

    def test_dwell_time_blocks_without_force(self):
        service, stub, gc = self.make(net_balance=-100)
        gc.set_config({"enabled": True, "min_switch_interval": 99999})
        self.assertEqual(service.sent, ["PCP01"])
        stub.net_balance = 100
        gc.tick()
        self.assertEqual(service.sent, ["PCP01"])   # held, not resent
        self.assertIn("anti-flap", gc.get_state()["last_run"]["note"])


class TestGridChargeApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "web_gridcharge.json")

    def test_get_without_configured(self):
        client = client_for(FakeService(), grid_charge=None)
        r = client.get("/api/grid-charge", headers=AUTH)
        self.assertEqual(r.status_code, 501)
        self.assertEqual(r.get_json()["code"], "no_grid_charge")

    def test_put_via_put_method_applies_immediately(self):
        service = FakeService()
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        client = client_for(service, grid_charge=gc)
        r = client.put("/api/grid-charge", data=json.dumps(
            {"enabled": True, "min_switch_interval": 0}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(service.sent, ["PCP01"])

    def test_put_rejects_invalid_config(self):
        service = FakeService()
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        client = client_for(service, grid_charge=gc)
        r = client.put("/api/grid-charge", data=json.dumps({"charge_pcp": "09"}),
                       content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "invalid_config")
        self.assertEqual(service.sent, [])

    def test_put_requires_auth(self):
        gc = GridChargeController(FakeService(), self.path, fetch_fn=FetchStub())
        client = client_for(FakeService(), grid_charge=gc)
        r = client.put("/api/grid-charge", data=json.dumps({"enabled": False}),
                       content_type="application/json")
        self.assertEqual(r.status_code, 401)

    def test_apply_now_forces_a_tick(self):
        service = FakeService()
        stub = FetchStub(-100)
        gc = GridChargeController(service, self.path, fetch_fn=stub)
        client = client_for(service, grid_charge=gc)
        client.put("/api/grid-charge", data=json.dumps(
            {"enabled": True, "min_switch_interval": 0}),
            content_type="application/json", headers=AUTH)
        service.sent.clear()
        stub.net_balance = 100
        r = post(client, "/api/grid-charge/apply-now", {"force": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(service.sent, ["PCP03"])

    def test_read_only_server_stores_but_does_not_apply(self):
        service = FakeService(allow_writes=False)
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        client = client_for(service, grid_charge=gc)
        r = client.put("/api/grid-charge", data=json.dumps(
            {"enabled": True, "min_switch_interval": 0}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["enabled"])
        self.assertEqual(service.sent, [])

    def test_enabling_grid_charge_is_refused_while_schedule_enabled(self):
        service = FakeService()
        frm, to = window_around_now()
        scheduler = Scheduler(service, os.path.join(self.dir, "web_schedule.json"))
        scheduler.set_state(True, [{"from": frm, "to": to, "pcp": "01", "why": "t"}])
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        client = client_for(service, scheduler, gc)
        r = client.put("/api/grid-charge", data=json.dumps(
            {"enabled": True}), content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "conflicting_automation")
        self.assertFalse(gc.get_state()["enabled"])

    def test_enabling_schedule_is_refused_while_grid_charge_enabled(self):
        service = FakeService()
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        scheduler = Scheduler(service, os.path.join(self.dir, "web_schedule.json"))
        client = client_for(service, scheduler, gc)
        frm, to = window_around_now()
        r = client.put("/api/schedule", data=json.dumps(
            {"enabled": True, "rules": [{"from": frm, "to": to, "pcp": "01", "why": "t"}]}),
            content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.get_json()["code"], "conflicting_automation")
        self.assertFalse(scheduler.get_state()["enabled"])

    def test_disabling_one_while_other_enabled_is_allowed(self):
        # The conflict check only fires when *enabling* -- turning one off
        # must never be blocked by the other being on.
        service = FakeService()
        gc = GridChargeController(service, self.path, fetch_fn=FetchStub(-100))
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        scheduler = Scheduler(service, os.path.join(self.dir, "web_schedule.json"))
        client = client_for(service, scheduler, gc)
        r = client.put("/api/schedule", data=json.dumps({"enabled": False, "rules": []}),
                       content_type="application/json", headers=AUTH)
        self.assertEqual(r.status_code, 200)


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
