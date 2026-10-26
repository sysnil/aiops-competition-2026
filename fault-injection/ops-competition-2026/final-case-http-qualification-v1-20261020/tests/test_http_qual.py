import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/http_qual.py'
PROJECT = SCRIPT.parents[2]
sys.path.insert(0, str(PROJECT / 'final-case-unified-execution-v2-20261019/scripts'))
sys.path.insert(0, str(PROJECT / 'final-case-formal-entry-v7-20261020/scripts'))
sys.path.insert(0, str(PROJECT / 'final-case-mechanism-qualification-v2-20261020/scripts'))
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('http_qual', SCRIPT)
http_qual = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = http_qual
spec.loader.exec_module(http_qual)
import issue_http_receipt


class HTTPQualificationTests(unittest.TestCase):
    def test_exact_case_action_and_source_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.py'
            source.write_text('pass\n')
            sources = {str(source): http_qual.sha(source)}
            run = 'SF-015-ATTEMPT-QUALTEST-ABCDEFGH'
            value = {'run_id': run, 'case': 'SF-015',
                     'data_root': str(http_qual.DATA / run),
                     'source_sha256': sources,
                     'action': {'run_id': run,
                                'action_id': run + '-HTTP',
                                'kind': 'HTTP', 'worker': 'k8s-master',
                                'mechanism': 'HTTPReqDelay',
                                'duration_seconds': 300,
                                'capability': 'BOUND_QUALIFIED_FAULT',
                                'entrypoint': str(http_qual.SHARED / 'http_gate.py')}}
            plan = root / 'plan.json'
            plan.write_text(json.dumps(value))
            with patch.object(http_qual, 'source_lock', return_value=sources):
                _, action = http_qual.load_plan(plan, http_qual.sha(plan))
                self.assertEqual(action['action_id'], run + '-HTTP')
                value['action']['duration_seconds'] = 60
                plan.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    http_qual.load_plan(plan, http_qual.sha(plan))

    def test_formal_minimum_cannot_drop_below_qualification_minimum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            qualification = 'SF-015-ATTEMPT-QUALTEST-ABCDEFGH'
            decision = root / 'decision.json'
            plan = root / 'plan.json'
            action = root / 'action.json'
            decision.write_text(json.dumps({
                'status': 'QUALIFIED_FOR_THIS_VERSION', 'blockers': [],
                'case': 'SF-015', 'qualification_id': qualification,
                'plan_sha256': 'a' * 64,
                'adapter_sha256': 'a' * 64,
                'duration_seconds': 300}))
            plan.write_text(json.dumps({'run_id': qualification,
                                        'action': {'min_fault_requests': 40}}))
            formal = {'run_id': 'SF-015-ATTEMPT-01',
                      'action_id': 'SF-015-ATTEMPT-01-HTTP',
                      'kind': 'HTTP', 'worker': 'k8s-master',
                      'mechanism': 'HTTPReqDelay',
                      'duration_seconds': 300,
                      'min_fault_requests': 1}
            action.write_text(json.dumps(formal))
            with patch.object(issue_http_receipt, 'sha',
                              return_value='a' * 64):
                with self.assertRaisesRegex(ValueError, 'MISMATCH'):
                    issue_http_receipt.issue(decision, plan, action)
                formal['min_fault_requests'] = 60
                action.write_text(json.dumps(formal))
                self.assertEqual(issue_http_receipt.issue(decision, plan,
                    action)['status'], 'QUALIFIED_FOR_THIS_VERSION')


if __name__ == '__main__':
    unittest.main()
