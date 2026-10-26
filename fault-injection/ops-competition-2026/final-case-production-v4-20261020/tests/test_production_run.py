import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE.parent / 'final-case-unified-execution-v2-20261019/scripts'))
sys.path.insert(0, str(HERE / 'scripts'))
from production_run import (action_result, checked_start, dynamic_safety,
                            schedule, stamp, utc)


class ProductionControlTests(unittest.TestCase):
    def test_six_chunks_cover_exact_half_open_window(self):
        begin = 1792454400
        value = schedule({'run_id': 'SF-013-ATTEMPT-03',
            'start_utc': utc(begin), 'window_end_utc': utc(begin + 1800)})
        self.assertEqual(len(value['windows']), 6)
        for index in range(6):
            row = value['windows'][f'chunk-{index:02d}']
            self.assertEqual(stamp(row['start_utc']), begin + index * 300)
            self.assertEqual(stamp(row['end_utc']), begin + (index + 1) * 300)

    def test_worker_effect_requires_actual_start_and_cleanup(self):
        planned = 1792455000
        action = {'run_id': 'SF-013-ATTEMPT-03',
                  'action_id': 'SF-013-ATTEMPT-03-POD',
                  'worker': 'k8s-worker2', 'planned_start_utc': utc(planned),
                  'duration_seconds': 300}
        effect = {'status': 'COMPETITION_BOUNDED_INJECTION_COMPLETED_NOT_YET_QUALIFIED',
                  'actual_start_utc': utc(planned + .2),
                  'duration_seconds': 300.1,
                  'cleanup': {'status': 'COMPETITION_CLEANUP_ACTIONS_VERIFIED'}}
        terminal = {'status': 'ACTION_TERMINAL', 'effect': effect}
        with patch('production_run.worker_command',
                   return_value=json.dumps(terminal)):
            self.assertEqual(action_result(action, 'a' * 64), terminal)
        effect['cleanup']['status'] = 'FAILED'
        with patch('production_run.worker_command',
                   return_value=json.dumps(terminal)):
            with self.assertRaisesRegex(RuntimeError, 'NOT_VERIFIED'):
                action_result(action, 'a' * 64)

    def test_start_deviation_aborts_at_effect_start(self):
        planned = utc(1792455000)
        self.assertAlmostEqual(checked_start(utc(1792455000.2), planned), .2)
        with self.assertRaisesRegex(RuntimeError, 'START_DEVIATION'):
            checked_start(utc(1792455002.6), planned)

    def test_dynamic_safety_fails_closed_during_audit_tail(self):
        with self.assertRaisesRegex(RuntimeError, 'DYNAMIC_SAFETY_BLOCKED'):
            dynamic_safety({'run_id': 'SF-013-ATTEMPT-05'},
                sampler=lambda _: {'status': 'STORAGE_BLOCKED'})
        self.assertEqual(dynamic_safety({}, sampler=lambda _: {
            'status': 'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS'})['status'],
            'DYNAMIC_STORAGE_AND_BASIC_HEALTH_PASS')


if __name__ == '__main__':
    unittest.main()
