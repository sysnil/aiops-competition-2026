from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))

from microirc_stage4f_runner import build_candidate_map, run
from test_openrca_xl_runner import fixture


class MicroIRCStage4FTests(unittest.TestCase):
    def test_public_four_layer_map_has_no_labels(self):
        mapping = build_candidate_map(fixture())
        self.assertEqual({item['layer'] for item in mapping['entities']},
                         {'SERVICE', 'POD', 'NODE', 'NETWORK_PATH'})
        self.assertFalse(mapping['has_evaluator_labels'])

    def test_both_modes_block_without_label_free_checkpoint(self):
        for mode in ('original', 'xl'):
            result = run(fixture(), mode)
            self.assertEqual(result['status'], 'BLOCKED_PREREQUISITE')
            self.assertEqual(result['ranking'], [])
            self.assertIsNone(result['root_entity'])


if __name__ == '__main__':
    unittest.main()
