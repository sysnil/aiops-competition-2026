import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from validate_openrca_smoke import allowed_evidence, validate
from test_openrca_xl_runner import fixture


class BlindOutputValidationTests(unittest.TestCase):
    def test_hallucinated_evidence_is_rejected(self):
        bundle = fixture()
        candidates, evidence = allowed_evidence(bundle)
        self.assertEqual(candidates['SERVICE/ad'], 'SERVICE')
        self.assertTrue(evidence['SERVICE/ad'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            public = root / 'bundle.json'
            prediction = root / 'prediction.json'
            audit = root / 'audit.json'
            public.write_text(json.dumps(bundle))
            prediction.write_text(json.dumps({
                'status': 'PASS', 'algorithm': 'OpenRCA-XL',
                'public_case_id': bundle['public_case_id'], 'variant': 'B',
                'input_sha256': hashlib.sha256(public.read_bytes()).hexdigest(),
                'ranking': [{'rank': 1, 'level': 'SERVICE', 'entity': 'SERVICE/ad',
                             'evidence_ids': ['invented'], 'reason': 'Public evidence'}]}))
            audit.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hallucinated entity or private evidence'):
                validate(public, prediction, audit)


if __name__ == '__main__':
    unittest.main()
