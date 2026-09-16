"""Offline coverage for the review's persistence and ownership fixes."""
import os
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from unittest.mock import Mock, patch
from notify import Notifier
from webapp.atomic_write import write_json_atomic

import usb_watchdog
from test_webapp import AUTH, FakeService, client_for
from webapp.grid_charge import GridChargeController
from webapp.scheduler import Scheduler
from webapp.service import InverterService


class TestReviewRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def test_disk_failure_preserves_ack_and_next_safety_write(self):
        svc = InverterService('/never-open', priorities_path=self.path('priorities'),
                              min_battery_voltage=25.5)
        svc._conn = Mock()
        svc._conn.send_set_command.return_value = '(ACK'
        svc.battery_voltage = Mock(return_value=27)
        sched = Scheduler(svc, self.path('schedule'))
        sched.set_state(True, [{'from': '00:00', 'to': '23:59', 'pcp': '03'}])
        with patch('webapp.service.write_json_atomic', side_effect=OSError('disk full')):
            result = sched.tick(force=True, now=datetime(2026, 9, 16, 12))
        self.assertTrue(result['applied'])
        self.assertIn('disk full', svc.latest()['priority_persistence_error'])
        svc.battery_voltage.return_value = 24
        sched.tick(now=datetime(2026, 9, 16, 12))
        self.assertEqual(svc._conn.send_set_command.call_args.args, ('PCP01',))

    def test_watchdog_budget_survives_poll_and_rearms_on_health(self):
        state = self.path('watchdog-state')
        with patch.multiple(usb_watchdog, Notifier=Mock(), load_config=Mock(return_value={}),
                            _adapter_hub=Mock(return_value='1-1'), _log=Mock()), \
                patch.object(usb_watchdog, '_service_health', return_value=(False, 'offline')) as health, \
                patch.object(usb_watchdog, 'restart_service') as restart, \
                patch.object(usb_watchdog, 'reset_bus') as reset, \
                patch('usb_watchdog.time.sleep'):
            self.assertEqual(usb_watchdog.check_once('unused', False, state), 2)
            self.assertEqual(usb_watchdog.check_once('unused', False, state), 2)
            self.assertEqual(restart.call_count, usb_watchdog.MAX_ATTEMPTS)
            self.assertEqual(reset.call_count, usb_watchdog.MAX_ATTEMPTS - 1)
            health.return_value = (True, 'healthy')
            self.assertEqual(usb_watchdog.check_once('unused', False, state), 0)
            self.assertEqual(usb_watchdog._recovery_attempts(state), 0)

    def test_simultaneous_enable_requests_keep_one_owner(self):
        service = FakeService()
        sched = Scheduler(service, self.path('schedule'))
        gc = GridChargeController(service, self.path('grid'),
                                  fetch_fn=lambda: (0, 0, 0, 'sample'))
        app = client_for(service, scheduler=sched, grid_charge=gc).application
        gate = threading.Barrier(2)
        def enable(route, body):
            with app.test_client() as client:
                gate.wait(timeout=3)
                return client.put(route, json=body, headers=AUTH).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(enable, '/api/schedule', {'enabled': True, 'rules': []})
            b = pool.submit(enable, '/api/grid-charge', {'enabled': True, 'mode': 'exclusive'})
            self.assertEqual(sorted([a.result(timeout=5), b.result(timeout=5)]), [200, 409])
        self.assertNotEqual(sched.get_state()['enabled'], gc.get_state()['enabled'])

    def test_drift_notification_deduplicates_retries_and_announces_recovery(self):
        config = self.path('web.json')
        write_json_atomic(config, {'token': 'test-only', 'http_port': 9090})
        notifier = Notifier({}, state_file=self.path('notify.json'))
        notifier.send = Mock(side_effect=[False, True, True])
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        def state(stuck, mode='B'):
            response.read.return_value = json.dumps({
                'ok': True, 'pop_drift_stuck': stuck, 'device_mode': mode}).encode()
        with patch('usb_watchdog.urllib.request.urlopen', return_value=response) as http:
            state(True)
            usb_watchdog._battery_window_alert(config, notifier)  # failed delivery
            usb_watchdog._battery_window_alert(config, notifier)  # retry succeeds
            usb_watchdog._battery_window_alert(config, notifier)  # standing fault
            self.assertEqual(notifier.send.call_count, 2)
            state(False, None)
            usb_watchdog._battery_window_alert(config, notifier)  # not recovery
            self.assertEqual(notifier.send.call_count, 2)
            state(False, 'L')
            usb_watchdog._battery_window_alert(config, notifier)
            usb_watchdog._battery_window_alert(config, notifier)
            self.assertEqual(notifier.send.call_count, 3)
            self.assertIn('/api/battery-window', http.call_args.args[0].full_url)
            self.assertEqual(http.call_args.kwargs['timeout'], 5.0)

    def test_unreachable_alert_api_preserves_fault_state(self):
        config = self.path('web.json')
        write_json_atomic(config, {'token': 'test-only'})
        notifier = Notifier({}, state_file=self.path('notify.json'))
        notifier.send = Mock(return_value=True)
        notifier.on_change('battery_pop_drift', 'stuck', 'fault')
        with patch('usb_watchdog.urllib.request.urlopen', side_effect=OSError('offline')), \
                patch('usb_watchdog._log'):
            usb_watchdog._battery_window_alert(config, notifier)
        self.assertEqual(notifier.send.call_count, 1)
        self.assertEqual(notifier._state()['battery_pop_drift']['value'], 'stuck')

    def test_healthy_watchdog_checks_controller_alert_without_reset(self):
        with patch.multiple(usb_watchdog, Notifier=Mock(), load_config=Mock(return_value={}),
                            _service_health=Mock(return_value=(True, 'healthy')), _log=Mock()), \
                patch.object(usb_watchdog, '_battery_window_alert') as alert, \
                patch.object(usb_watchdog, 'reset_bus') as reset:
            usb_watchdog.check_once('unused', False, self.path('state'))
        alert.assert_called_once()
        reset.assert_not_called()
