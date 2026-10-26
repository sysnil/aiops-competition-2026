import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from build_public_anomaly_fixture import build


class PublicAnomalyFixtureTests(unittest.TestCase):
    def test_fixture_is_anonymous_and_keeps_source_unchanged(self):
        bundle = {'schema_version': 1, 'public_only': True, 'variant': 'B',
                  'public_case_id': 'original',
                  'signals': {'SERVICE/z': {'traces': [
                      {'observed': True, 'abnormal': False} for _ in range(4)]},
                              'SERVICE/a': {'traces': [
                                  {'observed': True, 'abnormal': False} for _ in range(5)]}}}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, output, manifest = (root / name for name in
                                        ('source.json', 'fixture.json', 'manifest.json'))
            source.write_text(json.dumps(bundle))
            result = build(source, output, manifest)
            self.assertEqual(result['selected_public_entity'], 'SERVICE/a')
            self.assertEqual(result['signals_marked_abnormal'], 5)
            self.assertEqual(json.loads(source.read_text())['public_case_id'], 'original')
            fixture = json.loads(output.read_text())
            self.assertNotEqual(fixture['public_case_id'], 'original')
            self.assertNotIn('case_id', fixture)
            self.assertFalse(fixture['signals']['SERVICE/z']['traces'][0]['abnormal'])


if __name__ == '__main__':
    unittest.main()
