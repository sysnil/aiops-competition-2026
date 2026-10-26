from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from admission import admit
from evaluate import score_prediction
from pod_precheck import FAMILIES


def quality():
    return {'before_seconds': 600, 'during_seconds': 300, 'after_seconds': 600,
            'before_during_comparable': True, 'observability_pass': True,
            'public_private_leak_scan_pass': True, 'recovery_pass': True,
            'service_wide_replicas_stable': True, 'target_node_ready_during': True,
            'pod_uid_before': 'uid-a', 'pod_uid_during': 'uid-a',
            'pod_container_id_before': 'container-a',
            'pod_container_id_during': 'container-a',
            'pod_during_metric_sample_counts': {name: 20 for name in FAMILIES},
            'target_pod_recreated': False, 'path_evidence_comparable': True}


def bundle():
    return {'candidates': [{'entity': entity} for entity in (
        'SERVICE/catalog', 'POD/demo/catalog-a', 'NODE/worker-a',
        'NETWORK_PATH/worker-a/worker-b')]}


class AdmissionTests(unittest.TestCase):
    def test_service_case_admits(self):
        result = admit('F01', bundle(), quality(), {'root_layer': 'SERVICE',
                       'root_entity': 'SERVICE/catalog', 'injection_success': True})
        self.assertEqual(result['status'], 'ADMITTED_CASE')

    def test_pod_uid_change_invalidates_case(self):
        facts = quality()
        facts['pod_uid_during'] = 'replacement'
        result = admit('F02', bundle(), facts, {'root_layer': 'POD',
                       'root_entity': 'POD/demo/catalog-a', 'target_pod_uid': 'uid-a',
                       'injection_success': True},
                       {'status': 'POD_CANDIDATE_PRECHECK_PASS',
                        'candidate': 'POD/demo/catalog-a'})
        self.assertEqual(result['status'], 'INVALID_CASE')
        self.assertIn('POD_UID_CHANGED_OR_MISMATCHED', result['reasons'])

    def test_pod_during_cfs_gap_invalidates_case(self):
        facts = quality()
        facts['pod_during_metric_sample_counts'][FAMILIES[-1]] = 9
        result = admit('F02', bundle(), facts, {'root_layer': 'POD',
                       'root_entity': 'POD/demo/catalog-a', 'target_pod_uid': 'uid-a',
                       'injection_success': True},
                       {'status': 'POD_CANDIDATE_PRECHECK_PASS',
                        'candidate': 'POD/demo/catalog-a', 'container_id': 'container-a'})
        self.assertIn('POD_DURING_CFS_CPU_MEMORY_SAMPLES_INSUFFICIENT', result['reasons'])

    def test_pod_five_series_and_container_identity_admit(self):
        result = admit('F02', bundle(), quality(), {'root_layer': 'POD',
                       'root_entity': 'POD/demo/catalog-a', 'target_pod_uid': 'uid-a',
                       'injection_success': True},
                       {'status': 'POD_CANDIDATE_PRECHECK_PASS',
                        'candidate': 'POD/demo/catalog-a', 'container_id': 'container-a'})
        self.assertEqual(result['status'], 'ADMITTED_CASE')

    def test_path_without_pre_fault_proof_invalidates_case(self):
        result = admit('F04', bundle(), quality(), {'root_layer': 'NETWORK_PATH',
                       'root_entity': 'NETWORK_PATH/worker-a/worker-b',
                       'injection_success': True})
        self.assertIn('PATH_PRECHECK_MISSING', result['reasons'])

    def test_missing_public_candidate_is_case_invalid_not_model_failure(self):
        result = admit('F02', {'candidates': []}, quality(), {'root_layer': 'POD',
                       'root_entity': 'POD/demo/catalog-a', 'target_pod_uid': 'uid-a',
                       'injection_success': True},
                       {'status': 'POD_CANDIDATE_PRECHECK_PASS',
                        'candidate': 'POD/demo/catalog-a'})
        self.assertIn('ROOT_NOT_IN_PUBLIC_CANDIDATES', result['reasons'])


class ScoringTests(unittest.TestCase):
    GT = {'root_layer': 'NODE', 'root_entity': 'NODE/worker-a'}

    def test_wrong_layer_is_separate_from_wrong_entity(self):
        result = score_prediction({'ranking': [{'rank': 1, 'level': 'SERVICE',
                                                'entity': 'SERVICE/catalog'}]}, self.GT)
        self.assertEqual(result['hierarchical_state'], 'wrong_layer')
        self.assertIsNone(result['entity_at_1_conditioned_on_correct_layer'])

    def test_correct_layer_wrong_entity(self):
        result = score_prediction({'ranking': [{'rank': 1, 'level': 'NODE',
                                                'entity': 'NODE/worker-b'},
                                               {'rank': 2, 'level': 'NODE',
                                                'entity': 'NODE/worker-a'}]}, self.GT)
        self.assertEqual(result['hierarchical_state'], 'correct_layer_wrong_entity')
        self.assertEqual(result['entity_at_1_conditioned_on_correct_layer'], 0)
        self.assertEqual(result['entity_at_3'], 1)
        self.assertEqual(result['mrr'], 0.5)

    def test_exact_entity(self):
        result = score_prediction({'ranking': [{'rank': 1, 'level': 'NODE',
                                                'entity': 'NODE/worker-a'}]}, self.GT)
        self.assertEqual(result['hierarchical_state'], 'exact_entity')
        self.assertEqual(result['end_to_end_exact'], 1)

    def test_no_prediction_is_zero_not_missing(self):
        result = score_prediction({'ranking': []}, self.GT)
        self.assertEqual(result['hierarchical_state'], 'no_prediction')
        self.assertEqual(result['layer_at_1'], 0)
        self.assertEqual(result['end_to_end_exact'], 0)


if __name__ == '__main__':
    unittest.main()
