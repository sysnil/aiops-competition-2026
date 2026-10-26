from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from pod_cpu_exec import CPU_WORKERS, shell_workload


class PodCPUPlanTests(unittest.TestCase):
    def test_workload_is_bounded_and_stays_in_existing_pod(self):
        self.assertEqual(CPU_WORKERS, 4)
        command = shell_workload()
        self.assertIn('for i in 1 2 3 4', command)
        self.assertIn('trap cleanup EXIT HUP INT TERM', command)
        self.assertNotIn('cgroup.procs', command)


if __name__ == '__main__':
    unittest.main()
