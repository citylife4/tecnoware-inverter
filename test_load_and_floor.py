"""Heavy-load hold-off for the daytime window, and the low-battery latch.

Both added 2026-09-23 from WATCHLIST.md decisions 1 and 3.
"""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from test_webapp import FakeService
from webapp.battery_window import (BATTERY_POP, GRID_POP, BatteryWindow,
                                   DEFAULT_CONFIG, HARDWARE_OVERRIDE_CONFIRMATIONS,
                                   validate_config)
from webapp.safety import (FLOOR_RELEASE_CONFIRMATIONS, LowBatteryLatch,
                           apply_low_battery_floor)
from webapp.grid_charge import GridChargeController
from webapp.scheduler import Scheduler

NOON = datetime(2026, 9, 23, 12, 0)
NIGHT = datetime(2026, 9, 23, 2, 0)


class TestDaytimeHeavyLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(battery_voltage=26.5, output_load_w=46)
        self.external = {"n": 0}
        self.bw = BatteryWindow(
            self.service, os.path.join(self.tmp.name, "bw.json"),
            signal_source=lambda: (300.0, True),
            heavy_load_source=lambda w, s: self.external["n"])
        self.bw.set_config({"enabled": True, "min_switch_interval": 0,
                            "daytime_enabled": True,
                            "daytime_min_switch_interval": 0}, now=NOON)

    def test_opens_without_a_heavy_load(self):
        self.assertEqual(self.bw.get_state()["last_run"]["reason"], "daytime")
        self.assertEqual(self.service.sent[-1], "POP02")

    def test_two_heavy_battery_readings_close_the_window(self):
        self.service._device_mode = "B"
        self.service._output_load_w = 1200
        self.bw.tick(now=NOON)
        self.assertEqual(self.service.sent[-1], "POP02", "one reading is not enough")
        r = self.bw.tick(now=NOON)
        self.assertEqual(r["reason"], "daytime_heavy_load")
        self.assertEqual(r["target"], GRID_POP)
        self.assertEqual(self.service.sent[-1], "POP00")
        self.assertIsNotNone(self.bw.get_state()["heavy_load_until"])

    def test_single_compressor_inrush_does_not_close_it(self):
        """The fridge's inrush reaches 1129-1223 W for one sample."""
        self.service._device_mode = "B"
        self.service._output_load_w = 1223
        self.bw.tick(now=NOON)
        self.service._output_load_w = 46
        for _ in range(5):
            r = self.bw.tick(now=NOON)
        self.assertEqual(r["reason"], "daytime")

    def test_shelly_readings_in_bypass_block_entry(self):
        self.bw.set_config({"enabled": False}, now=NOON)
        self.service.sent.clear()
        self.external["n"] = 2
        self.bw.set_config({"enabled": True}, now=NOON)
        self.assertEqual(self.bw.get_state()["last_run"]["reason"], "daytime_heavy_load")
        self.assertNotIn("POP02", self.service.sent)

    def test_hold_off_expires(self):
        self.external["n"] = 2
        self.bw.tick(now=NOON)
        self.external["n"] = 0
        r = self.bw.tick(now=NOON + timedelta(minutes=59))
        self.assertEqual(r["reason"], "daytime_heavy_load")
        r = self.bw.tick(now=NOON + timedelta(minutes=61))
        self.assertEqual(r["reason"], "daytime")

    def test_daytime_hardware_override_arms_the_hold_off(self):
        self.service._device_mode = "L"
        for _ in range(HARDWARE_OVERRIDE_CONFIRMATIONS):
            self.bw.tick(now=NOON)
        self.assertIsNotNone(self.bw.get_state()["heavy_load_until"])

    def test_nightly_hardware_override_does_not(self):
        """The ~07:40 program-12 changeover ends the night; it says nothing
        about a heavy load."""
        bw = BatteryWindow(self.service, os.path.join(self.tmp.name, "n.json"))
        self.service._device_mode = None
        bw.set_config({"enabled": True, "min_switch_interval": 0}, now=NIGHT)
        self.service._device_mode = "L"
        for _ in range(HARDWARE_OVERRIDE_CONFIRMATIONS):
            bw.tick(now=NIGHT)
        self.assertTrue(bw.get_state()["recovering"])
        self.assertIsNone(bw.get_state()["heavy_load_until"])

    def test_a_failing_source_does_not_break_the_tick(self):
        def boom(w, s):
            raise RuntimeError("auto-energy down")
        self.bw._heavy_load_source = boom
        self.assertEqual(self.bw.tick(now=NOON)["reason"], "daytime")

    def history_with(self, *watts):
        now = datetime.now(timezone.utc)
        return [{"ts": (now - timedelta(seconds=10 * i)).isoformat(),
                 "ac_output_active_power": w} for i, w in enumerate(watts)]

    def test_samples_between_ticks_arm_it(self):
        """2026-09-24 18:55: heavy readings every ~20 s, none on a tick."""
        self.service._device_mode = "B"
        self.service._output_load_w = 45      # what the tick itself sees
        self.service.history = lambda: self.history_with(45, 1194, 1, 1986, 45)
        r = self.bw.tick(now=NOON)
        self.assertEqual(r["reason"], "daytime_heavy_load")

    def test_one_inrush_in_the_history_does_not(self):
        self.service._device_mode = "B"
        self.service.history = lambda: self.history_with(45, 1223, 46, 45, 45)
        self.assertEqual(self.bw.tick(now=NOON)["reason"], "daytime")

    def test_old_samples_age_out(self):
        old = datetime.now(timezone.utc) - timedelta(minutes=30)
        self.service.history = lambda: [
            {"ts": (old - timedelta(seconds=i)).isoformat(),
             "ac_output_active_power": 1200} for i in range(5)]
        self.assertEqual(self.bw.tick(now=NOON)["reason"], "daytime")

    def test_same_event_on_both_sources_counts_once(self):
        self.external["n"] = 1
        self.service.history = lambda: self.history_with(45, 1223, 45)
        self.assertEqual(self.bw.tick(now=NOON)["reason"], "daytime")

    def test_validation(self):
        with self.assertRaises(ValueError):
            validate_config({"daytime_heavy_load_w": 0})
        with self.assertRaises(ValueError):
            validate_config({"daytime_heavy_load_holdoff": -1})


class TestFloorDefault(unittest.TestCase):
    def test_floor_sits_above_program_12(self):
        """At 24.0 V it was identical to program 12, so the hardware always
        ended the night before the software floor could."""
        self.assertGreater(DEFAULT_CONFIG["floor_voltage"], 24.0)
        validate_config(dict(DEFAULT_CONFIG))


class TestLowBatteryLatch(unittest.TestCase):
    def setUp(self):
        self.service = FakeService(battery_voltage=26.0, min_battery_voltage=25.5)
        self.latch = LowBatteryLatch()

    def step(self, v, amps):
        self.service._battery_voltage = v
        self.service._charging_current = amps
        self.latch.observe(self.service)
        return apply_low_battery_floor(self.service, "03", latch=self.latch)[0]

    def test_holds_through_bulk_charging(self):
        """The 2026-09-16 failure: the charger raises the terminal voltage
        past min_battery_voltage at ~50% state of charge."""
        self.assertEqual(self.step(25.0, 0), "01")
        for v in (26.5, 27.2, 28.5, 29.0):
            self.assertEqual(self.step(v, 10), "01")

    def test_releases_once_the_charger_reaches_float(self):
        self.step(25.0, 0)
        for _ in range(FLOOR_RELEASE_CONFIRMATIONS - 1):
            self.assertEqual(self.step(27.4, 4), "01")
        self.assertEqual(self.step(27.4, 4), "03")
        self.assertFalse(self.latch.active)

    def test_charger_not_yet_running_is_not_float(self):
        self.step(25.0, 0)
        for _ in range(5):
            self.assertEqual(self.step(25.8, 0), "01")

    def test_one_corrupt_frame_does_not_release(self):
        self.step(25.0, 0)
        self.step(27.4, 4)
        self.step(27.4, 4)
        self.step(27.4, 10)          # still bulk: count restarts
        self.assertEqual(self.step(27.4, 4), "01")

    def test_without_a_latch_behaviour_is_unchanged(self):
        self.service._battery_voltage = 27.0
        self.assertEqual(apply_low_battery_floor(self.service, "03")[0], "03")

    def test_scheduler_holds_pcp01_while_charging(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        sched = Scheduler(self.service, os.path.join(tmp.name, "s.json"))
        sched.set_state(True, [{"from": "00:00", "to": "23:59", "pcp": "03"}])
        self.service._battery_voltage = 25.0
        sched.tick(force=True)
        self.assertEqual(self.service.sent[-1], "PCP01")
        self.service._battery_voltage = 27.1
        self.service._charging_current = 10
        sched.tick(force=True)
        self.assertEqual(self.service.sent[-1], "PCP01",
                         "the old stateless check would write PCP03 here")


class TestDaytimeLatchRelease(unittest.TestCase):
    """2026-09-26: the night ended on the floor at 03:10; the charger started
    at 08:22 and the latch released at 08:25 on 27.0 V of charging voltage.
    The daytime window put a ~50% pack back on battery and it hit 24.2 V."""

    MORNING = datetime(2026, 9, 26, 9, 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(battery_voltage=24.5, output_load_w=1)
        self.bw = BatteryWindow(
            self.service, os.path.join(self.tmp.name, "bw.json"),
            signal_source=lambda: (300.0, True))
        self.bw.set_config({"enabled": True, "min_switch_interval": 0,
                            "daytime_enabled": True,
                            "daytime_min_switch_interval": 0}, now=NIGHT)
        for _ in range(3):
            self.bw.tick(now=NIGHT)          # floor latches at 24.5 V
        self.assertTrue(self.bw.get_state()["recovering"])
        self.service._device_mode = "L"

    def ticks(self, v, amps, n=5):
        self.service._battery_voltage = v
        self.service._charging_current = amps
        for _ in range(n):
            r = self.bw.tick(now=self.MORNING)
        return r

    def test_bulk_charging_voltage_does_not_release(self):
        r = self.ticks(27.0, 10)
        self.assertTrue(self.bw.get_state()["recovering"])
        self.assertEqual(r["target"], GRID_POP)
        r = self.ticks(29.0, 8)
        self.assertTrue(self.bw.get_state()["recovering"])

    def test_releases_once_the_charger_reaches_float(self):
        self.ticks(29.0, 10)
        self.service.sent.clear()
        self.ticks(29.1, 4, n=3)
        self.assertFalse(self.bw.get_state()["recovering"])
        self.assertEqual(self.service.sent, ["POP02"])

    def test_rested_pack_with_the_charger_off_still_releases(self):
        """The 2026-09-15 fix must survive: a rested full pack, nothing
        charging overnight."""
        self.ticks(25.4, 0)
        self.assertFalse(self.bw.get_state()["recovering"])

    def test_unknown_current_does_not_release(self):
        self.ticks(27.4, None)
        self.assertTrue(self.bw.get_state()["recovering"])


class TestHeavyInputReadings(unittest.TestCase):
    def test_counts_heavy_shelly_readings(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        readings = iter([60.0, 1190.0, 55.0, 1250.0, 58.0])
        gc = GridChargeController(
            FakeService(), os.path.join(tmp.name, "gc.json"),
            fetch_fn=lambda: (100.0, next(readings), 150.0, None))
        for _ in range(5):
            gc.tick()
        self.assertEqual(gc.heavy_input_readings(800, 600), 2)
        self.assertEqual(gc.heavy_input_readings(1200, 600), 1)
        self.assertEqual(gc.heavy_input_readings(800, 0), 0)


class TestProfiles(unittest.TestCase):
    """The occupied/away switch, added 2026-09-29."""

    def setUp(self):
        import json
        from test_webapp import AUTH, client_for
        self.json = json
        self.auth = AUTH
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(battery_voltage=27.0)
        self.bw = BatteryWindow(self.service, os.path.join(self.tmp.name, "bw.json"))
        self.client = client_for(self.service, battery_window=self.bw)

    def put(self, name):
        return self.client.put("/api/battery-window/profile",
                               data=self.json.dumps({"profile": name}),
                               content_type="application/json", headers=self.auth)

    def test_occupied_is_shallow_and_has_no_daytime_window(self):
        body = self.put("occupied").get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["profile"], "occupied")
        self.assertEqual(body["config"]["floor_voltage"], 25.0)
        self.assertFalse(body["config"]["daytime_enabled"])
        self.assertTrue(body["config"]["enabled"])

    def test_away_is_deep_with_the_daytime_window(self):
        body = self.put("away").get_json()
        self.assertEqual(body["profile"], "away")
        self.assertEqual(body["config"]["floor_voltage"], 24.5)
        self.assertTrue(body["config"]["daytime_enabled"])

    def test_a_manual_edit_reads_as_custom(self):
        self.put("occupied")
        self.bw.set_config({"floor_voltage": 24.8})
        self.assertEqual(self.bw.get_state()["profile"], "custom")

    def test_switching_keeps_other_settings(self):
        self.bw.set_config({"from": "02:00", "resume_voltage": 25.4})
        cfg = self.put("occupied").get_json()["config"]
        self.assertEqual(cfg["from"], "02:00")
        self.assertEqual(cfg["resume_voltage"], 25.4)

    def test_unknown_profile_is_refused(self):
        r = self.put("holiday")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.get_json()["code"], "invalid_profile")

    def test_every_profile_passes_validation(self):
        from webapp.battery_window import PROFILES
        for values in PROFILES.values():
            validate_config(values)

    def test_every_profile_has_a_pt_label(self):
        from webapp import ui_labels
        from webapp.battery_window import CUSTOM_PROFILE, PROFILES
        self.assertEqual(set(ui_labels.PROFILE_LABELS_PT),
                         set(PROFILES) | {CUSTOM_PROFILE})

    def test_the_dashboard_renders_the_switch(self):
        from test_webapp import TOKEN
        with self.client.session_transaction() as sess:
            sess["authed"] = True
        html = self.client.get("/").get_data(as_text=True)
        self.assertIn('data-profile="occupied"', html)
        self.assertIn('"Casa vazia"', html)


class TestNightCharging(unittest.TestCase):
    """2026-10-08: the day's last PCP used to stand all night. Ending on
    PCP01 refilled the pack from the grid right after the nightly window,
    destroying the headroom for the next day's surplus."""

    def setUp(self):
        from test_webapp import FetchStub
        from webapp.grid_charge import GENERATING_CONFIRMATIONS
        self.n = GENERATING_CONFIRMATIONS
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "gc.json")
        self.service = FakeService(battery_voltage=27.4, min_battery_voltage=24.3,
                                   device_mode="L")
        self.stub = FetchStub(-200)                 # sunny, exporting
        self.gc = GridChargeController(self.service, self.path, fetch_fn=self.stub)
        self.gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.assertEqual(self.service.sent[-1], "PCP01")   # absorbing export

    def sunset(self):
        self.stub.net_balance = 90
        self.stub.ac_solar_w = 0.0
        for _ in range(self.n):
            self.gc.tick()

    def test_a_day_ending_in_pcp01_is_closed_with_pcp03(self):
        self.sunset()
        self.assertEqual(self.service.sent[-1], "PCP03")

    def test_planned_discharge_and_drift_do_not_charge_at_night(self):
        """24.5 V floor, then standby drift under min_battery_voltage: the
        10-08 case. No grid refill before dawn."""
        self.sunset()
        self.service.sent.clear()
        for v in (24.5, 24.3, 24.2, 24.1):
            self.service._battery_voltage = v
            self.gc.tick()
        self.assertEqual(self.service.sent, [])

    def test_a_deep_pack_charges_at_night_until_float(self):
        self.sunset()
        self.service.sent.clear()
        self.service._battery_voltage = 23.9
        self.gc.tick()
        self.assertEqual(self.service.sent, ["PCP01"])
        self.service._battery_voltage = 27.2       # bulk: keep charging
        self.service._charging_current = 10
        for _ in range(3):
            self.gc.tick()
        self.assertEqual(self.service.sent, ["PCP01"])
        self.service._battery_voltage = 27.4       # float
        self.service._charging_current = 4
        for _ in range(3):
            self.gc.tick()
        self.assertEqual(self.service.sent, ["PCP01", "PCP03"])

    def test_a_low_reading_on_battery_does_not_arm(self):
        """A pack on battery sags under load; that is the battery window's
        job, with its own floor."""
        self.sunset()
        self.service.sent.clear()
        self.service._device_mode = "B"
        self.service._battery_voltage = 23.5
        self.gc.tick()
        self.assertEqual(self.service.sent, [])

    def test_overdue_maintenance_charges_at_night(self):
        from datetime import timezone as tz
        self.sunset()
        self.service.sent.clear()
        self.service._battery_voltage = 25.2
        self.gc._last_float_at = datetime.now(tz.utc) - timedelta(hours=49)
        self.gc.tick()
        self.assertEqual(self.service.sent, ["PCP01"])
        self.assertTrue(self.gc.get_state()["battery_maintenance"]["latch_active"])

    def test_last_float_survives_a_restart(self):
        import json
        from datetime import timezone as tz
        old = datetime(2026, 10, 1, 12, 0, tzinfo=tz.utc)
        with open(self.path + ".state", "w") as fh:
            json.dump({"last_float_at": old.isoformat()}, fh)
        restarted = GridChargeController(self.service, self.path, fetch_fn=self.stub)
        self.assertEqual(restarted._last_float_at, old)

    def test_min_battery_voltage_still_arms_in_daylight(self):
        self.stub.net_balance = 200                 # importing: idle
        self.service._battery_voltage = 24.2
        for _ in range(3):
            self.gc.tick()
        self.assertEqual(self.service.sent[-1], "PCP01")
        self.assertTrue(self.gc.get_state()["battery_maintenance"]["latch_active"])


if __name__ == "__main__":
    unittest.main()
