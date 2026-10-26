import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/qualify_worker.py'
spec = importlib.util.spec_from_file_location('qualify_worker', SCRIPT)
qualify_worker = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = qualify_worker
spec.loader.exec_module(qualify_worker)


class WorkerQualificationTests(unittest.TestCase):
    def test_mechanism_allowlist(self):
        self.assertEqual(qualify_worker.ALLOWED[('SF-013', 'POD', 300)],
                         'k8s-worker2')
        self.assertNotIn(('SF-015', 'POD', 300), qualify_worker.ALLOWED)
        self.assertIsNone(qualify_worker.ID.fullmatch(
            'SF-013-ATTEMPT-03-POD'))

    def test_wrong_or_stale_permit_rejected_before_effect(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ident = 'QUAL-SF-013-POD300-V5-ABCDEFGH'
            registration = root / 'registration.json'
            registration.write_text(json.dumps({'id': ident, 'layer': 'POD',
                'duration_seconds': 300, 'host': 'k8s-worker2'}))
            permit = root / 'authorization.json'
            permit.write_text(json.dumps({'status': 'COMPETITION_PREFLIGHT_AUTHORIZED',
                'id': ident, 'registration_sha256': 'bad',
                'at_utc': '2026-10-20T00:00:00Z'}))
            output = root / 'output'
            with patch.object(qualify_worker, 'OPS', root), patch.object(
                    qualify_worker, 'QUAL_ROOT', root), patch.object(
                    qualify_worker.socket, 'gethostname', return_value='k8s-worker2'):
                with self.assertRaises(ValueError):
                    qualify_worker.validate(registration, permit, output,
                                            now=lambda: 1792454400)

    def test_exact_qualification_permit_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ident = 'QUAL-SF-013-POD300-V5-ABCDEFGH'
            registration = root / 'registration.json'
            registration.write_text(json.dumps({'id': ident, 'layer': 'POD',
                'duration_seconds': 300, 'host': 'k8s-worker2'}))
            private = root / 'runs' / ident
            private.mkdir(parents=True)
            permit = private / 'authorization.json'
            permit.write_text(json.dumps({
                'status': 'COMPETITION_PREFLIGHT_AUTHORIZED',
                'purpose': 'FINAL_CASE_V5_MECHANISM_QUALIFICATION',
                'id': ident,
                'registration_sha256': hashlib.sha256(
                    registration.read_bytes()).hexdigest(),
                'at_utc': '2026-10-20T00:00:00Z'}))
            output = root / 'qualification/private-attempts' / ident
            with patch.object(qualify_worker, 'OPS', root), patch.object(
                    qualify_worker, 'QUAL_ROOT', root), patch.object(
                    qualify_worker.socket, 'gethostname', return_value='k8s-worker2'):
                reg, auth = qualify_worker.validate(registration, permit, output,
                                                    now=lambda: 1792454405)
            self.assertEqual(reg['id'], ident)
            self.assertEqual(auth['purpose'],
                             'FINAL_CASE_V5_MECHANISM_QUALIFICATION')


if __name__ == '__main__':
    unittest.main()
