import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from prepare_full_nofault import source_lock
from full_window_rehearsal import capture_commands, schedule, verify_freeze
from archive_rehearsal import archive


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


class NoFaultPipelineTests(unittest.TestCase):
    def test_exact_six_chunks_and_capture_commands(self):
        start = 1792400000
        plan = schedule({'run_id': 'SF-017-ATTEMPT-MARKERTEST-ABCDEFGH',
                         'start_utc': iso(start),
                         'window_end_utc': iso(start + 1800)})
        self.assertEqual(len(plan['windows']), 6)
        self.assertEqual(plan['windows']['chunk-00']['start_utc'], iso(start))
        self.assertEqual(plan['windows']['chunk-05']['end_utc'], iso(start + 1800))
        with tempfile.TemporaryDirectory() as temporary:
            commands = capture_commands(Path(temporary),
                {'name': 'chunk-00', **plan['windows']['chunk-00']})
            self.assertEqual(len(commands), 3)
            self.assertIn('JAEGER_OPENSEARCH_STORAGE', commands['chunk-00-first'])

    def test_source_lock_has_capture_and_authorization(self):
        locked = source_lock()
        self.assertIn(str(ROOT / 'scripts/rehearsal_observer.py'), locked)
        self.assertTrue(any(path.endswith('/auth_protocol.py') for path in locked))
        self.assertTrue(any(path.endswith('/capture_live_c2.py') for path in locked))

    def test_freeze_rejects_source_drift_and_formal_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / 'manifest.json'
            manifest.write_text('{}')
            lock = root / 'lock.txt'
            lock.write_text('before')
            freeze = root / 'freeze.json'
            freeze.write_text(json.dumps({
                'status': 'FROZEN_HARMLESS_1800_CANDIDATE',
                'manifest_path': str(manifest), 'manifest_sha256': 'a' * 64,
                'real_faults_injected': 0, 'formal_attempt_created': False,
                'source_sha256': {str(lock): 'b' * 64}}))
            with self.assertRaisesRegex(ValueError, 'SOURCE_HASH_DRIFT'):
                verify_freeze(freeze, manifest, 'a' * 64)
            with self.assertRaisesRegex(ValueError, 'FREEZE_IDENTITY'):
                verify_freeze(freeze, manifest, 'c' * 64)

    def test_archive_rejects_non_rehearsal_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'SOURCE_SCOPE'):
                archive(Path(temporary))


if __name__ == '__main__':
    unittest.main()
