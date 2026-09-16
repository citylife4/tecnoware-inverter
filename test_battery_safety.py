"""Offline regressions for ownership across failures and controller shutdown."""
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from test_webapp import FakeService
from webapp.atomic_write import write_json_atomic
from webapp.battery_window import BatteryWindow
from webapp.battery_window import HARDWARE_OVERRIDE_CONFIRMATIONS, POP_DRIFT_CONFIRMATIONS, POP_DRIFT_MAX_WRITES

NIGHT = datetime(2026, 9, 16, 23)


class TestBatterySafety(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "window.json")
        self.service = FakeService(battery_voltage=27)
        self.bw = BatteryWindow(self.service, self.path)

    def enter(self):
        self.bw.set_config({"enabled": True, "min_switch_interval": 0}, now=NIGHT)
        self.assertEqual(self.service.sent[-1], "POP02")
        self.service.sent.clear()

    def runtime(self):
        self.assertTrue(os.path.exists(self.path + ".state"),
                        "battery entry must persist its hand-back obligation")
        with open(self.path + ".state") as fh:
            return json.load(fh)

    def test_obligation_is_on_disk_before_the_write(self):
        send = self.service.send_set
        def checked(command, source):
            if command == "POP02":
                self.assertTrue(self.runtime()["release_pending"])
            return send(command, source)
        with patch.object(self.service, "send_set", side_effect=checked):
            self.enter()

    def test_failed_persistence_prevents_battery_entry(self):
        def write(path, *args, **kwargs):
            if path.endswith(".state"):
                raise OSError("disk full")
            return write_json_atomic(path, *args, **kwargs)
        with patch("webapp.battery_window.write_json_atomic", side_effect=write):
            self.bw.set_config({"enabled": True}, now=NIGHT)
        self.assertNotIn("POP02", self.service.sent)
        self.assertIn("cannot persist", self.bw.get_state()["last_run"]["note"])

    def test_lost_entry_reply_survives_restart_and_disabled_config(self):
        self.service._ack = False
        self.enter()
        write_json_atomic(self.path, {"enabled": False})
        restarted = BatteryWindow(self.service, self.path)
        self.service._ack = True
        restarted.tick(now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])
        self.assertFalse(self.runtime()["release_pending"])

    def test_line_mode_does_not_cancel_handback(self):
        self.enter()
        self.service._device_mode = "L"
        self.bw.set_config({"enabled": False}, now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])

    def test_failed_handback_remains_due_after_restart(self):
        self.enter()
        self.service._ack = False
        self.bw.set_config({"enabled": False}, now=NIGHT)
        self.assertTrue(self.runtime()["release_pending"])
        self.service.sent.clear()
        restarted = BatteryWindow(self.service, self.path)
        self.service._ack = True
        restarted.tick(now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])

    def test_loop_failure_is_visible_and_returns_to_utility(self):
        self.enter()
        self.bw._stop = Mock()
        self.bw._stop.is_set.return_value = False
        self.bw._stop.wait.return_value = True
        with patch.object(self.service, "mode", side_effect=RuntimeError("broken mode")), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.bw._loop()
        self.assertIn("broken mode", stderr.getvalue())
        self.assertEqual(self.service.sent, ["POP00"])
        self.assertIn("broken mode", self.bw.get_state()["loop_error"])
        self.assertEqual(self.bw.get_state()["last_run"]["reason"], "error")

    def test_failed_emergency_release_retries_before_deciding(self):
        self.enter()
        self.service._ack = False
        self.bw._stop = Mock()
        self.bw._stop.is_set.return_value = False
        self.bw._stop.wait.return_value = True
        with patch("sys.stderr", new_callable=io.StringIO), \
                patch.object(self.service, "mode", side_effect=RuntimeError("failed decision")):
            self.bw._loop()
        self.service.sent.clear()
        self.service._ack = True
        with patch.object(self.service, "mode", side_effect=RuntimeError("still broken")):
            with self.assertRaises(RuntimeError):
                self.bw.tick(now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])

    def test_shutdown_lock_timeout_preserves_obligation(self):
        self.enter()
        with patch.object(self.bw, "_lock") as lock, \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            lock.acquire.return_value = False
            self.bw.stop()
        self.assertEqual(self.service.sent, [])
        self.assertTrue(self.runtime()["release_pending"])
        self.assertIn("blocked", stderr.getvalue())

    def test_shutdown_releases_once_and_prevents_later_entry(self):
        self.enter()
        self.bw.stop()
        self.bw.stop()
        self.bw.tick(force=True, now=NIGHT)
        self.bw.set_config({"enabled": True}, now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])
        self.assertFalse(self.runtime()["release_pending"])

    def test_shutdown_lost_reply_keeps_obligation_and_logs(self):
        self.enter()
        self.service._ack = False
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            self.bw.stop()
        self.assertTrue(self.runtime()["release_pending"])
        self.assertIn("not acknowledged", stderr.getvalue())

    def test_read_only_shutdown_does_not_write(self):
        self.enter()
        self.service.allow_writes = False
        self.bw.stop()
        self.assertEqual(self.service.sent, [])
        self.assertTrue(self.runtime()["release_pending"])

    def test_transient_line_transfer_does_not_spend_the_night(self):
        self.enter()
        for mode in ("L", "B", "L", None, "L", "B"):
            self.service._device_mode = mode
            self.bw.tick(now=NIGHT, force=True)
        self.assertFalse(self.bw.get_state()["recovering"])
        self.assertNotIn("POP00", self.service.sent)

    def test_sustained_line_transfer_latches_on_third_observation(self):
        self.enter()
        self.service._device_mode = "L"
        for _ in range(HARDWARE_OVERRIDE_CONFIRMATIONS - 1):
            self.bw.tick(now=NIGHT, force=True)
            self.assertEqual(self.service.sent, [])
            self.assertFalse(self.bw.get_state()["recovering"])
        self.bw.tick(now=NIGHT)
        self.assertEqual(self.service.sent, ["POP00"])
        self.assertTrue(self.bw.get_state()["recovering"])

    def test_low_or_unknown_voltage_transfer_is_immediate(self):
        for voltage in (24.0, None):
            with self.subTest(voltage=voltage):
                self.setUp()
                self.enter()
                self.service._device_mode = "L"
                self.service._battery_voltage = voltage
                self.bw.tick(now=NIGHT)
                self.assertEqual(self.service.sent, ["POP00"])

    def test_drift_alert_survives_unknown_mode_until_line_confirmation(self):
        noon = datetime(2026, 9, 16, 12)
        self.bw.set_config({"enabled": True}, now=noon)
        self.service._device_mode = "B"
        for _ in range(POP_DRIFT_CONFIRMATIONS * (POP_DRIFT_MAX_WRITES + 1)):
            self.bw.tick(now=noon)
        self.assertIn("pop_drift_stuck", self.bw.get_state())
        self.assertTrue(self.bw.get_state()["pop_drift_stuck"])
        self.bw = BatteryWindow(self.service, self.path)
        self.assertTrue(self.bw.get_state()["pop_drift_stuck"])
        self.service._device_mode = None
        self.bw.tick(now=noon)
        self.assertTrue(self.bw.get_state()["pop_drift_stuck"])
        self.service._device_mode = "L"
        self.bw.tick(now=noon)
        self.assertFalse(self.bw.get_state()["pop_drift_stuck"])


if __name__ == "__main__":
    unittest.main()
