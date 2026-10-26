import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'algorithms'))

from contracts import CONTRACTS, freeze, lint, verify
from simple_rca_calibrated import run, score_signals


def prepare_freeze_inputs(root):
    provenance_path = root / 'source/provenance.json'
    provenance = json.loads(provenance_path.read_text())
    archive_root = root / 'source/author-archives'
    archive_root.mkdir()
    provenance['source_archive_root'] = str(archive_root)
    for item in provenance['repositories']:
        archive = archive_root / item['archive']
        archive.write_bytes(item['algorithm'].encode())
        item['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
    provenance_path.write_text(json.dumps(provenance))
    readiness = root / 'readiness'
    readiness.mkdir()
    decision = readiness / 'executable-readiness-yyt-2026-10-03.json'
    route = readiness / 'f04-route-proof-podprobe-2026-10-03'
    route.mkdir()
    proof = route / 'public_verified_route_map.json'
    proof.write_text('{}')
    code = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in ('design', 'scripts', 'algorithms')
            for path in (root / folder).glob('*') if path.is_file()}
    payload = {'status': 'READY_TO_FREEZE', 'blockers': [], 'faults_injected': 0,
               'contracts_frozen': False, 'code_sha256': code,
               'live_converter': {'trace_source': 'JAEGER_OPENSEARCH_STORAGE'},
               'source_provenance_sha256': hashlib.sha256(provenance_path.read_bytes()).hexdigest(),
               'f04_proof_sha256': hashlib.sha256(proof.read_bytes()).hexdigest()}
    decision.write_text(json.dumps(payload))
    return decision


def metric_signals(count, abnormal):
    return [{'family': f'metric_{index}', 'observed': True,
             'abnormal': index < abnormal} for index in range(count)]


class ContractTests(unittest.TestCase):
    def test_design_lints(self):
        self.assertEqual(len(lint(ROOT)), 6)

    def test_freeze_refuses_rewrite_and_detects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'design').mkdir()
            (root / 'algorithms').mkdir()
            (root / 'scripts').mkdir()
            (root / 'source').mkdir()
            for name in CONTRACTS:
                shutil.copy2(ROOT / 'design' / name, root / 'design' / name)
            shutil.copy2(ROOT / 'algorithms/simple_rca_calibrated.py',
                         root / 'algorithms/simple_rca_calibrated.py')
            shutil.copy2(ROOT / 'scripts/contracts.py', root / 'scripts/contracts.py')
            shutil.copy2(ROOT / 'source/provenance.json', root / 'source/provenance.json')
            decision = prepare_freeze_inputs(root)
            ready_payload = decision.read_text()
            decision.write_text(json.dumps({'status': 'PRE_FREEZE_BLOCKED',
                                            'blockers': ['MISSING_ENDPOINT'],
                                            'faults_injected': 0,
                                            'contracts_frozen': False}))
            with self.assertRaisesRegex(ValueError, 'does not authorize freeze'):
                freeze(root, decision)
            decision.write_text(ready_payload)
            script = root / 'scripts/contracts.py'
            script.write_text(script.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'code or source hash is stale'):
                freeze(root, decision)
            shutil.copy2(ROOT / 'scripts/contracts.py', script)
            freeze(root, decision)
            frozen = verify(root)
            self.assertEqual(frozen['status'], 'STAGE4F_CONTRACTS_FROZEN_BEFORE_FAULT')
            self.assertEqual(frozen['trace_source'], 'JAEGER_OPENSEARCH_STORAGE')
            with self.assertRaisesRegex(ValueError, 'already frozen'):
                freeze(root, decision)
            path = root / 'design/case-plan.yaml'
            path.chmod(0o644)
            path.write_text(path.read_text() + '\n')
            with self.assertRaisesRegex(ValueError, 'changed'):
                verify(root)


class CalibratedSimpleTests(unittest.TestCase):
    def test_small_denominator_abstains(self):
        bundle = {
            'schema_version': 1, 'public_only': True,
            'public_case_id': '94f51b5a-55cf-4f7c-bf3d-f25e66163390', 'variant': 'B',
            'candidates': [{'entity': 'POD/demo/collector-a', 'layer': 'POD'},
                           {'entity': 'SERVICE/catalog', 'layer': 'SERVICE'}],
            'signals': {
                'POD/demo/collector-a': {'metrics': metric_signals(2, 1)},
                'SERVICE/catalog': {'metrics': metric_signals(6, 2)},
            },
            'clean_null_window_maxima': {'SERVICE': [0.25] * 8, 'POD': [0.25] * 8},
        }
        result = run(bundle)
        self.assertEqual(result['root_entity'], 'SERVICE/catalog')
        self.assertEqual(result['abstained'][0]['status'], 'INSUFFICIENT_EVIDENCE')

    def test_missing_signal_is_not_normal(self):
        count, score, modality = score_signals({'metrics': [
            {'family': 'cpu', 'observed': True, 'abnormal': True},
            {'family': 'cgroup', 'observed': False},
        ]})
        self.assertEqual(count, 1)
        self.assertAlmostEqual(score, 2 / 3)
        self.assertEqual(modality['metrics'], score)

    def test_duplicate_family_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate signal family'):
            score_signals({'metrics': [{'family': 'cpu', 'observed': True, 'abnormal': True}],
                           'logs': [{'family': 'cpu', 'observed': True, 'abnormal': False}]})

    def test_missing_clean_null_abstains(self):
        bundle = {'schema_version': 1, 'public_only': True,
                  'public_case_id': '94f51b5a-55cf-4f7c-bf3d-f25e66163390', 'variant': 'B',
                  'candidates': [{'entity': 'NODE/worker-a', 'layer': 'NODE'}],
                  'signals': {'NODE/worker-a': {'metrics': metric_signals(4, 4)}},
                  'clean_null_window_maxima': {'NODE': [0.2] * 7}}
        self.assertEqual(run(bundle)['abstained'][0]['status'], 'INSUFFICIENT_TYPE_CALIBRATION')


if __name__ == '__main__':
    unittest.main()
