"""A slow probe cannot shift the next planned sample start."""

import importlib.util
from pathlib import Path
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'scripts/probe_network.py'
spec = importlib.util.spec_from_file_location('phase4_probe', SOURCE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeSchedulerTest(unittest.TestCase):
    def test_slow_first_sample_does_not_delay_next(self):
        started = {}
        persisted = []
        def task(index, scheduled):
            started[index] = time.time()
            if index == 0:
                time.sleep(.10)
            return {'index': index, 'scheduled': scheduled}
        begin = time.time() + .02
        rows = probe.schedule_samples(begin, 3, .035, task, persisted.append)
        self.assertEqual([r['index'] for r in rows], [0, 1, 2])
        self.assertEqual(len(persisted), 3)
        self.assertLess(started[1] - (begin + .035), .03)


if __name__ == '__main__':
    unittest.main()
