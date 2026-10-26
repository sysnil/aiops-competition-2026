import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from run_public_method import status_from_prediction, validate_bundle
from test_openrca_xl_runner import fixture


class PublicMethodRunTests(unittest.TestCase):
    def test_author_statuses_are_not_replaced_with_rankings(self):
        self.assertEqual(status_from_prediction({'status': 'NO_ELIGIBLE_CANDIDATE',
                                                 'ranking': []}),
                         ('NO_PREDICTION', 'NO_ELIGIBLE_CANDIDATE'))
        self.assertEqual(status_from_prediction({'status': 'BLOCKED_PREREQUISITE',
                                                 'reason': 'No label-free checkpoint'}),
                         ('BLOCKED_PREREQUISITE', 'No label-free checkpoint'))
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            status_from_prediction({'status': 'PASS', 'ranking': []})

    def test_algorithm_visible_path_cannot_contain_case_label(self):
        with tempfile.TemporaryDirectory() as temp:
            private = Path(temp) / 'F01'
            private.mkdir()
            bundle = fixture()
            bundle['windows'] = {'before': {}, 'during': {}}
            source = private / 'bundle.json'
            source.write_text(json.dumps(bundle))
            with self.assertRaisesRegex(ValueError, 'private F01-F04 label'):
                validate_bundle(source, 'B')


if __name__ == '__main__':
    unittest.main()
