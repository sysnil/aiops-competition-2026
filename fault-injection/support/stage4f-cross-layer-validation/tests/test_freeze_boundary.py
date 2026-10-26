import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from contracts import freeze as freeze_contracts
from freeze_preflight import PRECHECK_FILE, freeze as freeze_preflight
from freeze_public import freeze as freeze_public, verify as verify_public
from freeze_predictions import METHODS, NETWORK_A_METHODS, freeze as freeze_predictions
from evaluate import evaluate
from network_path_precheck import validate as network_validate
from pod_precheck import FAMILIES, validate as pod_validate
from service_node_precheck import node as node_validate, service as service_validate
from test_network_path_precheck import fixture as network_fixture
from test_pod_precheck import fixture as pod_fixture
from test_contracts_and_simple import prepare_freeze_inputs


class FreezeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('design', 'scripts', 'algorithms', 'source'):
            shutil.copytree(ROOT / name, self.root / name)
        decision = prepare_freeze_inputs(self.root)
        freeze_contracts(self.root, decision)
        self.candidates = [{'entity': 'SERVICE/demo', 'layer': 'SERVICE'},
                           {'entity': 'POD/demo/pod-a', 'layer': 'POD'},
                           {'entity': 'NODE/worker-a', 'layer': 'NODE'},
                           {'entity': 'NETWORK_PATH/worker3/worker2', 'layer': 'NETWORK_PATH'}]
        self.null = {layer: [0.2] * 8 for layer in ('SERVICE', 'POD', 'NODE', 'NETWORK_PATH')}
        for case, (name, status) in PRECHECK_FILE.items():
            public_case_id = f'00000000-0000-4000-8000-{int(case[-2:]):012d}'
            directory = self.root / 'preflight' / case
            directory.mkdir(parents=True)
            (directory / 'candidate-universe.json').write_text(json.dumps({
                'public_only': True, 'case_id': case, 'public_case_id': public_case_id,
                'candidates': self.candidates, 'relations': []}))
            (directory / 'clean-null-calibration.json').write_text(json.dumps({
                'public_only': True, 'case_id': case,
                'variants': {variant: {
                    'clean_null_window_maxima': {layer: values for layer, values in self.null.items()
                                                  if variant == 'B' or layer != 'NETWORK_PATH'},
                    'clean_null_pagerank_maxima': {layer: [0.01] * 8 for layer in self.null
                                                    if variant == 'B' or layer != 'NETWORK_PATH'}}
                    for variant in ('A', 'B')}}))
            window = {'start_unix_ns': 0, 'end_unix_ns': 600_000_000_000}
            if case == 'F01':
                snapshot = {'schema_version': 1, 'public_only': True, 'target_service': 'demo',
                            'candidate_entities': ['SERVICE/demo'], 'rollout_pending': False,
                            'before_window': window,
                            'pods': [{'uid': uid, 'service': 'demo', 'ready': True,
                                      'stable_through_before': True} for uid in ('a', 'b')],
                            'pod_metric_sample_counts': {uid: {'cpu_usage': 30} for uid in ('a', 'b')}}
                precheck = service_validate(snapshot)
            elif case == 'F02':
                snapshot = pod_fixture()
                snapshot['target_pod'].update({'namespace': 'demo', 'name': 'pod-a', 'uid': 'uid-a'})
                for item in snapshot['pod_observations']:
                    item.update({'namespace': 'demo', 'name': 'pod-a', 'uid': 'uid-a'})
                snapshot['candidate_entities'] = ['POD/demo/pod-a']
                precheck = pod_validate(snapshot)
            elif case == 'F03':
                snapshot = {'schema_version': 1, 'public_only': True, 'target_node': 'worker-a',
                            'candidate_entities': ['NODE/worker-a'], 'node_ready_before': True,
                            'rollout_pending': False, 'before_window': window,
                            'pods': [{'node': 'worker-a', 'service': name, 'ready': True,
                                      'business_service': True} for name in ('cart', 'currency')],
                            'node_metric_sample_counts': {'cpu_usage': 30, 'cpu_time': 30,
                                                          'memory_available': 30}}
                precheck = node_validate(snapshot)
            else:
                snapshot = network_fixture()
                precheck = network_validate(snapshot)
            source = directory / 'public-snapshot.json'
            source.write_text(json.dumps(snapshot))
            precheck['input_sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
            (directory / name).write_text(json.dumps(precheck))
            freeze_preflight(self.root, case)
            for variant in ('variant-a', 'variant-b'):
                public = self.root / 'cases' / case / 'public' / variant
                public.mkdir(parents=True)
                candidates = [item for item in self.candidates
                              if variant == 'variant-b' or item['layer'] != 'NETWORK_PATH']
                (public / 'bundle.json').write_text(json.dumps({
                    'schema_version': 1, 'public_only': True, 'public_case_id': public_case_id,
                    'variant': variant[-1].upper(), 'candidates': candidates, 'relations': [],
                    'clean_null_window_maxima': {layer: values for layer, values in self.null.items()
                                                  if variant == 'variant-b' or layer != 'NETWORK_PATH'},
                    'clean_null_pagerank_maxima': {layer: [0.01] * 8 for layer in self.null
                                                    if variant == 'variant-b' or layer != 'NETWORK_PATH'}}))
                (public / 'case-quality.json').write_text(json.dumps({
                    'before_seconds': 600, 'during_seconds': 300,
                    'before_during_comparable': True, 'observability_pass': True,
                    'public_private_leak_scan_pass': True}))
                for filename in ('service-topology.json', 'pod-placement.json', 'node-placement.json'):
                    (public / filename).write_text('{}')
                for folder in ('metrics', 'logs', 'traces', 'kubernetes'):
                    (public / folder).mkdir()
                    (public / folder / 'sample.json').write_text('{}')
                if variant == 'variant-b':
                    (public / 'network-paths.json').write_text('{}')
                    (public / 'normal-network-observations.json').write_text('{}')
                    mapping = {'traversing_edges': precheck['traversing_edges'],
                               'control_edges': precheck['control_edges']} if case == 'F04' else {}
                    (public / 'edge-path-map.json').write_text(json.dumps(mapping))

    def test_public_freeze_then_detect_mutation(self):
        result = freeze_public(self.root)
        self.assertEqual(result['status'], 'STAGE4F_PUBLIC_BUNDLES_FROZEN_BEFORE_INFERENCE')
        verify_public(self.root)
        path = self.root / 'cases/F02/public/variant-b/bundle.json'
        path.write_text(path.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_public(self.root)

    def test_private_token_rejects_public_freeze(self):
        path = self.root / 'cases/F04/public/variant-b/network-paths.json'
        path.write_text('{"device":"VM1"}')
        with self.assertRaisesRegex(ValueError, 'Private token'):
            freeze_public(self.root)

    def test_new_public_file_after_freeze_rejects(self):
        freeze_public(self.root)
        path = self.root / 'cases/F01/public/variant-b/metrics/extra.json'
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'file set changed'):
            verify_public(self.root)

    def test_post_fault_candidate_expansion_rejects(self):
        path = self.root / 'cases/F02/public/variant-b/bundle.json'
        bundle = json.loads(path.read_text())
        bundle['candidates'].append({'entity': 'POD/demo/new', 'layer': 'POD'})
        path.write_text(json.dumps(bundle))
        with self.assertRaisesRegex(ValueError, 'differ from pre-fault'):
            freeze_public(self.root)

    def test_synthetic_blind_pipeline(self):
        public = freeze_public(self.root)
        roots = {'F01': ('SERVICE', 'SERVICE/demo'),
                 'F02': ('POD', 'POD/demo/pod-a'),
                 'F03': ('NODE', 'NODE/worker-a'),
                 'F04': ('NETWORK_PATH', 'NETWORK_PATH/worker3/worker2')}
        for case, (layer, entity) in roots.items():
            for variant, methods in (('variant-b', METHODS),
                                     ('variant-a', NETWORK_A_METHODS)):
                for method in methods:
                    directory = self.root / 'runs' / case / variant / method
                    directory.mkdir(parents=True)
                    top_entity = entity if variant == 'variant-b' else 'SERVICE/demo'
                    top_layer = layer if variant == 'variant-b' else 'SERVICE'
                    bundle_name = f'cases/{case}/public/{variant}/bundle.json'
                    (directory / 'run-manifest.json').write_text(json.dumps({
                        'case_id': case, 'variant': variant[-1].upper(), 'algorithm': method,
                        'public_bundle_sha256': public['files'][bundle_name]['sha256'],
                        'status': 'PASS', 'public_bundle_only': True,
                        'after_data_excluded': True, 'network_context_consumed': variant == 'variant-b'}))
                    (directory / 'prediction.json').write_text(json.dumps({
                        'status': 'PASS', 'public_case_id':
                        f'00000000-0000-4000-8000-{int(case[-2:]):012d}',
                        'root_layer': top_layer, 'root_entity': top_entity,
                        'ranking': [{'rank': 1, 'level': top_layer, 'entity': top_entity}]}))
                    (directory / 'isolation-audit.json').write_text(json.dumps({
                        'evaluator_gt_read_denied': True, 'after_data_read_denied': True,
                        'stage_case_label_hidden': True, 'design_read_denied': True}))
                    for filename in ('command.txt', 'stdout.log', 'stderr.log', 'runtime.json'):
                        (directory / filename).write_text('{}')
        freeze_predictions(self.root)
        for case, (layer, entity) in roots.items():
            private = self.root / 'evaluator_gt' / case
            private.mkdir(parents=True)
            (private / 'ground_truth.yaml').write_text(yaml.safe_dump({
                'root_layer': layer, 'root_entity': entity, 'target_pod_uid': 'uid-a',
                'injection_success': True}))
            (private / 'admission-quality.json').write_text(json.dumps({
                'after_seconds': 600, 'recovery_pass': True,
                'service_wide_replicas_stable': True, 'target_node_ready_during': True,
                'pod_uid_before': 'uid-a', 'pod_uid_during': 'uid-a',
                'pod_container_id_before': 'container-001',
                'pod_container_id_during': 'container-001',
                'pod_during_metric_sample_counts': {name: 20 for name in FAMILIES},
                'target_pod_recreated': False, 'path_evidence_comparable': True}))
        result = evaluate(self.root)
        self.assertEqual(result['admitted_cases'], 4)
        self.assertEqual(result['methods'][0]['end_to_end_exact'], 1.0)
        self.assertTrue((self.root / 'reports/variant-a-vs-b.csv').is_file())


if __name__ == '__main__':
    unittest.main()
