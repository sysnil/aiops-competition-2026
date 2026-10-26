import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


project = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project / 'final-case-unified-execution-v2-20261019/scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import normal_workload


class NormalWorkloadTests(unittest.TestCase):
    def test_expected_business_errors_do_not_abort_complete_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'new-output'
            module = SimpleNamespace(run=lambda args: {
                'status': 'LOAD_COMPLETED_WITH_ERRORS',
                'planned_sessions': 100, 'recorded_sessions': 100})
            with patch.object(normal_workload, 'load_real_business_module',
                              return_value=module):
                result = normal_workload.run('2026-10-20T00:00:00Z', 1,
                                             output)
            self.assertEqual(result['status'], 'LOAD_COMPLETED_WITH_ERRORS')


if __name__ == '__main__':
    unittest.main()
