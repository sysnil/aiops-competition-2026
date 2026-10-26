import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))

from openrca_xl_tools import PublicEvidence


class PublicEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / 'bundle.json'
        self.bundle = {'schema_version': 1, 'public_only': True,
                       'public_case_id': '17610177-6e38-4858-b108-4fc4d5c28075',
                       'candidates': [{'entity': 'SERVICE/frontend'},
                                      {'entity': 'NETWORK_PATH/worker-a/worker-b'}],
                       'observations': {'metrics': {'SERVICE/frontend': [
                           {'phase': 'before', 'value': 1}, {'phase': 'during', 'value': 2}]},
                           'network': {'NETWORK_PATH/worker-a/worker-b': [
                               {'phase': 'before', 'packets': 100}]}},
                       'relations': [{'source': 'SERVICE_EDGE/frontend/catalog',
                                      'type': 'TRAVERSES',
                                      'target': 'NETWORK_PATH/worker-a/worker-b'},
                                     {'source': 'SERVICE_EDGE/frontend/cart',
                                      'type': 'POSSIBLY_TRAVERSES',
                                      'target': 'NETWORK_PATH/worker-a/worker-b'}]}
        self.file.write_text(json.dumps(self.bundle))

    def test_public_tools_page_and_verify_path_membership(self):
        tools = PublicEvidence(self.file)
        page = tools.get_service_metrics('frontend', offset=1, limit=1)
        self.assertEqual(page['rows'][0]['value'], 2)
        self.assertEqual(page['total'], 2)
        self.assertEqual(len(tools.get_path_edges('NETWORK_PATH/worker-a/worker-b')), 1)

    def test_after_phase_is_rejected(self):
        self.bundle['observations']['metrics']['SERVICE/frontend'].append({'phase': 'after', 'value': 3})
        self.file.write_text(json.dumps(self.bundle))
        with self.assertRaisesRegex(ValueError, 'After'):
            PublicEvidence(self.file).get_service_metrics('frontend')

    def test_stage_case_label_is_rejected(self):
        self.bundle['case_id'] = 'F04'
        self.file.write_text(json.dumps(self.bundle))
        with self.assertRaisesRegex(ValueError, 'anonymous'):
            PublicEvidence(self.file)


if __name__ == '__main__':
    unittest.main()
