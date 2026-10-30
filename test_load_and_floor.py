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

    def arm(self, v=25.0):
        """Three confirmed readings at or under the floor (2026-10-12)."""
        for _ in range(3):
            last = self.step(v, 0)
        return last

    def test_holds_through_bulk_charging(self):
        """The 2026-09-16 failure: the charger raises the terminal voltage
        past min_battery_voltage at ~50% state of charge."""
        self.assertEqual(self.arm(), "01")
        for v in (26.5, 27.2, 28.5, 29.0):
            self.assertEqual(self.step(v, 10), "01")

    def test_releases_once_the_charger_reaches_float(self):
        self.arm()
        for _ in range(FLOOR_RELEASE_CONFIRMATIONS - 1):
            self.assertEqual(self.step(27.4, 4), "01")
        self.assertEqual(self.step(27.4, 4), "03")
        self.assertFalse(self.latch.active)

    def test_charger_not_yet_running_is_not_float(self):
        self.arm()
        for _ in range(5):
            self.assertEqual(self.step(25.8, 0), "01")

    def test_one_corrupt_frame_does_not_release(self):
        self.arm()
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
        for _ in range(3):
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
        self.ticks(27.4, 4, n=3)            # float, not 29 V absorption
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


class TestCorruptFrames(unittest.TestCase):
    """2026-10-11 16:30:54: a line-mode frame read V=0.0 / I=100 and armed
    the latch on its own, leaving PCP01 standing at sunset."""

    def setUp(self):
        self.service = FakeService(battery_voltage=27.4, min_battery_voltage=24.0,
                                   device_mode="L")
        self.latch = LowBatteryLatch()

    def feed(self, *volts):
        for v in volts:
            self.service._battery_voltage = v
            apply_low_battery_floor(self.service, "03", latch=self.latch)

    def test_a_single_corrupt_frame_does_not_arm(self):
        self.feed(0.0)
        self.assertFalse(self.latch.active)

    def test_implausible_frames_never_count(self):
        self.feed(0.0, 4.0, 0.0, 4.0)
        self.assertFalse(self.latch.active)

    def test_one_genuine_low_reading_does_not_arm(self):
        self.feed(23.9, 25.5, 23.9, 25.5)
        self.assertFalse(self.latch.active)

    def test_three_genuine_low_readings_arm(self):
        self.feed(23.9, 23.9, 23.9)
        self.assertTrue(self.latch.active)

    def test_a_corrupt_frame_neither_counts_nor_resets(self):
        self.feed(23.9, 23.9, 0.0, 23.9)
        self.assertTrue(self.latch.active)

    def test_reading_state_does_not_count(self):
        for _ in range(5):
            self.service._battery_voltage = 23.9
            apply_low_battery_floor(self.service, "03", latch=self.latch, count=False)
        self.assertFalse(self.latch.active)

    def test_a_corrupt_frame_at_night_does_not_charge(self):
        from test_webapp import FetchStub
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stub = FetchStub(90)
        stub.ac_solar_w = 0.0
        gc = GridChargeController(self.service, os.path.join(tmp.name, "gc.json"),
                                  fetch_fn=stub)
        gc.set_config({"enabled": True, "min_switch_interval": 0})
        self.service._battery_voltage = 24.4      # after a night floor
        gc.tick()
        self.service.sent.clear()
        self.service._battery_voltage = 0.0       # one corrupt frame
        gc.tick()
        self.service._battery_voltage = 24.4
        for _ in range(3):
            gc.tick()
        self.assertNotIn("PCP01", self.service.sent)


class TestOccupiedRecharge(unittest.TestCase):
    """2026-10-13: under `occupied` the pack sat at 24.2 V (~45%) with no
    surplus to refill it, and the night would have entered battery mode at
    24.2 V against a 25.0 V floor."""

    def setUp(self):
        from test_webapp import FetchStub
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = FakeService(battery_voltage=24.2, min_battery_voltage=24.0,
                                   device_mode="L")
        self.bw = BatteryWindow(self.service, os.path.join(self.tmp.name, "bw.json"))
        self.stub = FetchStub(200)                 # daylight, importing: idle
        self.gc = GridChargeController(self.service, os.path.join(self.tmp.name, "gc.json"),
                                       fetch_fn=self.stub)
        self.gc.recharge_floor_source = self.bw.recharge_below_v

    def run_gc(self, n=4):
        self.gc.set_config({"enabled": True, "min_switch_interval": 0})
        for _ in range(n):
            self.gc.tick()

    def test_occupied_recharges_a_half_empty_pack(self):
        self.bw.apply_profile("occupied", now=NOON)
        self.run_gc()
        self.assertEqual(self.service.sent[-1], "PCP01")
        self.assertEqual(self.gc.get_state()["battery_maintenance"]["recharge_below_v"], 24.8)

    def test_away_waits_for_surplus(self):
        self.bw.apply_profile("away", now=NOON)
        self.run_gc()
        self.assertEqual(self.service.sent[-1], "PCP03")

    def test_the_profile_cannot_go_below_the_hard_minimum(self):
        self.service.min_battery_voltage = 24.5
        self.bw.apply_profile("away", now=NOON)      # 24.0 < 24.5
        self.assertEqual(self.gc._daylight_floor(), 24.5)

    def test_no_entry_near_the_floor(self):
        self.service._device_mode = None
        self.bw.apply_profile("occupied", now=NIGHT)
        r = self.bw.tick(now=NIGHT)
        self.assertEqual(r["reason"], "too_low_to_start")
        self.assertNotIn("POP02", self.service.sent)

    def test_entry_well_above_the_floor(self):
        self.service._device_mode = None
        self.service._battery_voltage = 25.6
        self.bw.apply_profile("occupied", now=NIGHT)
        self.assertIn("POP02", self.service.sent)

    def test_a_discharge_in_progress_still_runs_to_the_floor(self):
        self.service._device_mode = None
        self.service._battery_voltage = 25.6
        self.bw.apply_profile("occupied", now=NIGHT)
        self.service._battery_voltage = 25.1        # inside the margin now
        r = self.bw.tick(now=NIGHT)
        self.assertEqual(r["reason"], "window")

    def test_no_lock_order_deadlock_between_the_controllers(self):
        """2026-10-13 15:11: grid_charge held its lock and asked the battery
        window for the threshold (taking that lock), while the battery
        window held its lock and asked grid_charge is_absorbing_export.
        Both froze and every API request hung. The threshold read must not
        need the battery window's lock."""
        import threading
        self.bw.apply_profile("occupied", now=NOON)
        self.gc.set_config({"enabled": True, "min_switch_interval": 0})
        holding = threading.Event()
        release = threading.Event()

        def hold_window_lock():
            with self.bw._lock:
                holding.set()
                release.wait(5)

        t = threading.Thread(target=hold_window_lock)
        t.start()
        holding.wait(5)
        done = threading.Event()
        threading.Thread(target=lambda: (self.gc.tick(), done.set())).start()
        finished = done.wait(2)
        release.set()
        t.join()
        self.assertTrue(finished, "grid_charge blocked on the battery window's lock")

    def test_recharge_above_the_floor_is_refused(self):
        with self.assertRaises(ValueError):
            validate_config({"floor_voltage": 24.5, "recharge_below_v": 24.8})


class TestAbsorptionIsNotFloat(unittest.TestCase):
    """2026-10-30: absorption holds 29.0 V at ~4 A (coarse reading), which
    passed ">= 27.0 V and <= 4 A". Latches released and the maintenance
    clock reset mid-charge; the pack sat ~50-75% for two weeks."""

    def setUp(self):
        self.service = FakeService(battery_voltage=29.0, min_battery_voltage=24.0,
                                   device_mode="L")
        self.service._charging_current = 4

    def test_absorption_is_not_float(self):
        from webapp.safety import at_float
        self.assertFalse(at_float(self.service))

    def test_real_float_is_float(self):
        from webapp.safety import at_float
        self.service._battery_voltage = 27.4
        self.assertTrue(at_float(self.service))

    def test_the_latch_holds_through_absorption(self):
        latch = LowBatteryLatch()
        latch.arm()
        for _ in range(5):
            latch.observe(self.service)            # 29.0 V, 4 A
        self.assertTrue(latch.active)
        self.service._battery_voltage = 27.4
        for _ in range(3):
            latch.observe(self.service)
        self.assertFalse(latch.active)

    def test_absorption_does_not_reset_the_maintenance_clock(self):
        from test_webapp import FetchStub
        from datetime import timezone as tz
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        gc = GridChargeController(self.service, os.path.join(tmp.name, "gc.json"),
                                  fetch_fn=FetchStub(-200))
        old = datetime.now(tz.utc) - timedelta(hours=30)
        gc._last_float_at = old
        gc.tick()                                  # 29.0 V absorption
        self.assertEqual(gc._last_float_at, old)
        self.service._battery_voltage = 27.4       # float
        gc.tick()
        self.assertGreater(gc._last_float_at, old)


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
        for _ in range(3):
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

    def test_first_daylight_tick_under_the_threshold_holds_until_float(self):
        """2026-10-09 08:16: PCP01 on 24.2 V, then PCP03 30 s later at 27.0 V
        because the latch had not armed."""
        self.sunset()
        self.service._battery_voltage = 24.2
        self.stub.net_balance = 200            # sunrise, importing: idle
        self.stub.ac_solar_w = 40.0
        for _ in range(self.n + 3):
            self.gc.tick()
        self.assertEqual(self.service.sent[-1], "PCP01")
        self.service.sent.clear()
        self.service._battery_voltage = 27.0   # the charger's own voltage
        self.service._charging_current = 10
        for _ in range(3):
            self.gc.tick()
        self.assertEqual(self.service.sent, [])     # still PCP01, no flip
        self.assertTrue(self.gc.get_state()["battery_maintenance"]["latch_active"])

    def test_a_battery_mode_sag_in_daylight_does_not_arm(self):
        self.stub.net_balance = 200
        self.service._device_mode = "B"
        self.service._battery_voltage = 23.9
        self.gc.tick()
        self.assertFalse(self.gc.get_state()["battery_maintenance"]["latch_active"])

    def test_min_battery_voltage_still_arms_in_daylight(self):
        self.stub.net_balance = 200                 # importing: idle
        self.service._battery_voltage = 24.2
        for _ in range(4):
            self.gc.tick()
        self.assertEqual(self.service.sent[-1], "PCP01")
        self.assertTrue(self.gc.get_state()["battery_maintenance"]["latch_active"])


if __name__ == "__main__":
    unittest.main()
