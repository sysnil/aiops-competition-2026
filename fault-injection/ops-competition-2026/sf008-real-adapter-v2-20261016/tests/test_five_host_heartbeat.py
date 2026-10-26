import importlib.util
from pathlib import Path
import shlex
import unittest
from unittest.mock import patch


FILE = Path(__file__).resolve().parents[1] / 'scripts/five_host_heartbeat.py'
SPEC = importlib.util.spec_from_file_location('sf008_five_host_heartbeat', FILE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FiveHostObligations(unittest.TestCase):
    def test_abort_stops_fault_first_then_preserves_capture_terminal_window(self):
        with patch.object(MODULE.subprocess, 'run') as command, \
             patch.object(MODULE, 'unit_active', side_effect=[True, False]), \
             patch.object(MODULE, 'terminal_status', return_value=None), \
             patch.object(MODULE.time, 'monotonic', return_value=0), \
             patch.object(MODULE.time, 'sleep'):
            command.return_value.returncode = 0
            command.return_value.stderr = ''
            result = MODULE.stop_controller()
        self.assertEqual(result['fault_controller_exit_code'], 0)
        self.assertEqual(command.call_count, 2)
        self.assertEqual(command.call_args_list[0].args[0][-1], MODULE.FORMAL_UNIT)
        self.assertEqual(command.call_args_list[1].args[0][-1], MODULE.CAPTURE_UNIT)

    def test_short_background_disk_fluctuation_is_not_extrapolated(self):
        previous = {host: {'root_free_bytes': 69_000_000_000,
            **({'data_free_bytes': 56_000_000_000} if MODULE.HOSTS[host][2] else {})}
            for host in MODULE.HOSTS}
        current = {host: dict(disks) for host, disks in previous.items()}
        current['worker2']['root_free_bytes'] -= 44_000_000
        with patch.object(MODULE.time, 'monotonic', return_value=1.6), \
             patch.object(MODULE, 'remote', side_effect=lambda host: current[host]):
            _, _, forecast, decision = MODULE.sample([(0.0, previous)], 0.0, 2500)
        self.assertEqual(forecast['worker2']['root_free_bytes'], 64 * 1024**2)
        self.assertEqual(decision['status'], 'DYNAMIC_STORAGE_PASS')

    def test_sustained_decline_still_stops_before_five_gb(self):
        previous = {host: {'root_free_bytes': 69_000_000_000,
            **({'data_free_bytes': 56_000_000_000} if MODULE.HOSTS[host][2] else {})}
            for host in MODULE.HOSTS}
        current = {host: dict(disks) for host, disks in previous.items()}
        current['worker2']['data_free_bytes'] -= 2_000_000_000
        with patch.object(MODULE.time, 'monotonic', return_value=65.0), \
             patch.object(MODULE, 'remote', side_effect=lambda host: current[host]):
            _, _, forecast, decision = MODULE.sample([(0.0, previous)], 0.0, 2500)
        self.assertEqual(decision['status'], 'STOP_NEW_WORK')

    def test_heartbeat_transport_quotes_remote_python_as_one_command(self):
        with patch.object(MODULE.subprocess, 'run') as command:
            MODULE.publish({'status': 'DYNAMIC_STORAGE_PASS'})
        remote = shlex.split(command.call_args.args[0][-1])
        self.assertEqual(remote[:2], ['python3', '-c'])
        self.assertIn('sys.stdin.read()', remote[2])
        self.assertIn('DYNAMIC_STORAGE_PASS', command.call_args.kwargs['input'])

    def test_all_actual_mounts_are_included(self):
        values = MODULE.commitments()
        self.assertEqual(set(values), {'master', 'worker1', 'worker2',
                                       'worker3', 'vm1'})
        self.assertEqual(values['master']['data_free_bytes'], MODULE.FORECAST_CASE)
        self.assertEqual(values['worker1']['data_free_bytes'], MODULE.FORECAST_CASE)
        self.assertEqual(values['worker2']['data_free_bytes'], MODULE.FORECAST_CASE)
        self.assertEqual(values['worker3']['data_free_bytes'], 0)
        self.assertNotIn('data_free_bytes', values['vm1'])


if __name__ == '__main__':
    unittest.main()
