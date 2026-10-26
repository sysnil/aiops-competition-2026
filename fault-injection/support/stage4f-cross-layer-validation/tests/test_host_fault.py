from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from host_fault import (DURATION_SECONDS, PATH_DESTINATION_CIDR,
                        PATH_INTERFACE, path_qdisc_commands)


class HostFaultPlanTests(unittest.TestCase):
    def test_path_filter_targets_only_worker2_pod_cidr(self):
        self.assertEqual(DURATION_SECONDS, 300)
        commands = path_qdisc_commands()
        self.assertEqual(commands[0][:7], ['tc', 'qdisc', 'replace', 'dev',
                                            PATH_INTERFACE, 'root', 'handle'])
        self.assertEqual(commands[0][-16:], ['0'] * 16)
        self.assertIn('netem', commands[1])
        self.assertIn('100ms', commands[1])
        self.assertEqual(commands[2][-3:], [PATH_DESTINATION_CIDR, 'flowid', '1:3'])


if __name__ == '__main__':
    unittest.main()
