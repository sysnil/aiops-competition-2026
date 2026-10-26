import sys
import unittest
from pathlib import Path


ops = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ops / 'scripts'))
sys.path.insert(0, str(ops / 'final-case-unified-execution-v2-20261019/scripts'))
sys.path.insert(0, str(ops / 'final-case-formal-entry-v7-20261020/scripts'))
sys.path.insert(0, str(ops / 'final-case-mechanism-qualification-v3-20261020/scripts'))
sys.path.insert(0, str(ops / 'final-case-http-qualification-v1-20261020/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_formal import worker_evidence_paths


class MultiActionReceiptTests(unittest.TestCase):
    def test_worker_receives_all_manifest_receipts_and_own_registration(self):
        actions = [
            {'worker': 'k8s-master', 'qualification': {'receipt_path': '/runs/http.json'}},
            {'worker': 'k8s-worker3', 'qualified_registration': '/runs/service-reg.json',
             'qualification': {'receipt_path': '/runs/service.json'}},
        ]
        self.assertEqual(worker_evidence_paths(actions, 'k8s-worker3'),
                         [Path('/runs/http.json'), Path('/runs/service-reg.json'),
                          Path('/runs/service.json')])


if __name__ == '__main__':
    unittest.main()
