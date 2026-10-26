import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from assess_executable_readiness import validate_openrca_pair
from build_public_anomaly_fixture import build


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value) + '\n')


class OpenRCAReadinessPairTests(unittest.TestCase):
    def test_clean_abstention_plus_real_ranked_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            live, fixture, construction = (root / name for name in
                                           ('live.json', 'fixture.json', 'construction.json'))
            clean_prediction, clean_audit = root / 'clean-prediction.json', root / 'clean-audit.json'
            fixture_prediction, fixture_audit = root / 'fixture-prediction.json', root / 'fixture-audit.json'
            entity = 'SERVICE/frontend'
            signals = [{'family': 'trace.edge-' + str(index),
                        'observed': True, 'abnormal': False} for index in range(4)]
            save(live, {'schema_version': 1, 'public_only': True, 'variant': 'B',
                        'public_case_id': '00000000-0000-4000-8000-000000000001',
                        'candidates': [{'entity': entity, 'layer': 'SERVICE'}],
                        'signals': {entity: {'traces': signals}}})
            build(live, fixture, construction)
            fixture_data = json.loads(fixture.read_text())
            metadata = {'transport': 'https', 'endpoint_host': 'independent.test',
                        'model': 'real-model'}
            save(clean_prediction, {'algorithm': 'OpenRCA-XL',
                                    'method_kind': 'adapted_llm_agent',
                                    'status': 'NO_PREDICTION', 'ranking': [],
                                    'root_layer': None, 'root_entity': None,
                                    'input_sha256': digest(live), 'llm_metadata': metadata})
            save(clean_audit, {'status': 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_VALID_ABSTENTION',
                               'input_sha256': digest(live),
                               'prediction_sha256': digest(clean_prediction),
                               'endpoint_host': 'independent.test',
                               'public_read_allowed': True, 'home_read_denied': True,
                               'other_temp_read_denied': True, 'gt_read': False})
            evidence = [hashlib.sha256((entity + '|traces|' + row['family']).encode()).hexdigest()[:16]
                        for row in signals]
            save(fixture_prediction, {'algorithm': 'OpenRCA-XL', 'status': 'PASS',
                                      'public_case_id': fixture_data['public_case_id'],
                                      'variant': 'B', 'input_sha256': digest(fixture),
                                      'root_layer': 'SERVICE', 'root_entity': entity,
                                      'llm_metadata': metadata,
                                      'ranking': [{'rank': 1, 'level': 'SERVICE',
                                                   'entity': entity, 'reason': 'Public evidence',
                                                   'evidence_ids': evidence}]})
            save(fixture_audit, {'status': 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_PASS',
                                 'input_sha256': digest(fixture),
                                 'prediction_sha256': digest(fixture_prediction),
                                 'endpoint_host': 'independent.test',
                                 'public_read_allowed': True, 'home_read_denied': True,
                                 'other_temp_read_denied': True, 'gt_read': False})
            result = validate_openrca_pair(live, clean_prediction, clean_audit,
                                           fixture, fixture_prediction, fixture_audit,
                                           construction)
            self.assertEqual(result['clean_live_status'], 'VALID_CLEAN_ABSTENTION')
            self.assertEqual(result['fixture_evidence_references'], 4)
            altered = json.loads(fixture.read_text())
            altered['signals'][entity]['traces'][0]['abnormal'] = False
            save(fixture, altered)
            with self.assertRaisesRegex(ValueError, 'provenance'):
                validate_openrca_pair(live, clean_prediction, clean_audit,
                                       fixture, fixture_prediction, fixture_audit,
                                       construction)


if __name__ == '__main__':
    unittest.main()
