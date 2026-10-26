import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'scripts/first_fault_gate.py'
SPEC = importlib.util.spec_from_file_location('first_fault_gate', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EarlyFullAuditGate(unittest.TestCase):
    def setUp(self):
        self.schedule = {'start_utc': '2026-10-15T10:00:00Z', 'duration_seconds': 1800,
                         'faults_injected': 0}
        self.quality = {'status': 'CHUNK_QUALITY_PASS', 'chunk': 'chunk-00',
                        'completed_utc': '2026-10-15T10:09:40Z',
                        'completion_lag_seconds': {'plus240': 252}}
        self.safety = {'status': 'LIVE_ENVIRONMENT_PASS', 'at_utc': '2026-10-15T10:09:40Z'}
        self.initial = {'refused': {'grpc': 0, 'http': 0}}
        self.current = {'refused': {'grpc': 0, 'http': 0}, 'at_utc': '2026-10-15T10:09:40Z'}

    def check(self, quality=None, current=None, at='2026-10-15T10:09:41Z'):
        return MODULE.decide(self.schedule, self.quality if quality is None else quality,
                             self.safety, self.initial,
                             self.current if current is None else current,
                             '2026-10-15T10:10:00Z', at)

    def test_full_audit_early_enough_can_continue_to_other_gates(self):
        result = self.check()
        self.assertEqual(result['status'], 'EARLY_FULL_AUDIT_CANDIDATE_PASS')
        self.assertFalse(result['fault_authorization_issued'])

    def test_late_or_missing_audit_rejects_and_cancels(self):
        late = dict(self.quality, completed_utc='2026-10-15T10:09:50Z')
        self.assertTrue(self.check(late)['cancel_pending_timer_on_reject'])
        self.assertIn('FIRST_CHUNK_FULL_AUDIT_NOT_PASS', self.check({})['reasons'])

    def test_refused_increment_and_deadline_reject(self):
        raised = {'refused': {'grpc': 1, 'http': 0}, 'at_utc': '2026-10-15T10:09:40Z'}
        self.assertIn('COLLECTOR_REFUSAL_CHANGED', self.check(current=raised)['reasons'])
        self.assertIn('AUTHORIZATION_DEADLINE_MISSED',
                      self.check(at='2026-10-15T10:09:56Z')['reasons'])


if __name__ == '__main__':
    unittest.main()
