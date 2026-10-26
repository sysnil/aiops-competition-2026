import importlib.util
import unittest
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / 'scripts/qualified_adapter.py'
SPEC = importlib.util.spec_from_file_location('qualified_adapter_v5', PATH)
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)


class WatchdogTokenTests(unittest.TestCase):
    def test_unique_outputs_have_distinct_tokens(self):
        root = ADAPTER.ROOT / 'qualification'
        first = ADAPTER.selftest_token(root / 'SF-FINAL-WATCHDOG-A.json',
                                       'k8s-worker2')
        second = ADAPTER.selftest_token(root / 'SF-FINAL-WATCHDOG-B.json',
                                        'k8s-worker2')
        self.assertNotEqual(first, second)
        self.assertEqual(first, ADAPTER.selftest_token(
            root / 'SF-FINAL-WATCHDOG-A.json', 'k8s-worker2'))
        self.assertNotEqual(first, ADAPTER.selftest_token(
            root / 'SF-FINAL-WATCHDOG-A.json', 'k8s-worker3'))

    def test_historical_or_unscoped_output_is_rejected(self):
        with self.assertRaises(RuntimeError):
            ADAPTER.selftest_token(ADAPTER.ROOT / 'qualification/old.json',
                                   'k8s-worker2')
        with self.assertRaises(RuntimeError):
            ADAPTER.selftest_token(Path('/tmp/SF-FINAL-WATCHDOG-A.json'),
                                   'k8s-worker2')


if __name__ == '__main__':
    unittest.main()
