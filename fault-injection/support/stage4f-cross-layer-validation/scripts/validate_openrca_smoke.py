#!/usr/bin/env python3
"""Independently validate one GT-free OpenRCA-XL endpoint smoke artifact."""

import argparse
import hashlib
import json
from pathlib import Path
import re


LAYERS = {'SERVICE', 'POD', 'NODE', 'NETWORK_PATH'}
PRIVATE_TEXT = re.compile(r'(?i)(?:\bVM1\b|gretap|netem|evaluator_gt|'
                          r'fault[_ -]?target|fault[_ -]?type|injection[_ -]?command|\bF0[1-8]\b)')


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def allowed_evidence(bundle):
    candidates = {item['entity']: item['layer'] for item in bundle['candidates']}
    evidence = {entity: set() for entity in candidates}
    for entity, modalities in bundle.get('signals', {}).items():
        if entity not in evidence:
            raise ValueError('Signal belongs to a noncandidate entity')
        for modality, rows in modalities.items():
            for row in rows:
                if row.get('observed') is True:
                    key = hashlib.sha256((entity + '|' + modality + '|' +
                                          row['family']).encode()).hexdigest()[:16]
                    evidence[entity].add(key)
    context = bundle.get('network_context', {})
    path = context.get('network_path_entity')
    if path:
        if path not in evidence or candidates[path] != 'NETWORK_PATH':
            raise ValueError('Public network context lacks a typed candidate')
        for row in context.get('route_evidence', []):
            evidence[path].add(row['id'])
    return candidates, evidence


def validate(bundle_file, prediction_file, audit_file):
    bundle = json.loads(bundle_file.read_text())
    prediction = json.loads(prediction_file.read_text())
    audit = json.loads(audit_file.read_text())
    if bundle.get('schema_version') != 1 or bundle.get('public_only') is not True or (
            'case_id' in bundle):
        raise ValueError('Inference input was not an anonymous public bundle')
    candidates, evidence = allowed_evidence(bundle)
    ranking = prediction.get('ranking')
    if (prediction.get('status') != 'PASS' or prediction.get('algorithm') != 'OpenRCA-XL' or
            not isinstance(ranking, list) or not ranking or
            prediction.get('public_case_id') != bundle.get('public_case_id') or
            prediction.get('variant') != bundle.get('variant') or
            prediction.get('input_sha256') != sha256(bundle_file)):
        raise ValueError('Prediction identity, status or input hash is invalid')
    seen = set()
    cited = 0
    for expected_rank, row in enumerate(ranking, 1):
        entity = row.get('entity')
        refs = row.get('evidence_ids')
        if (row.get('rank') != expected_rank or entity not in candidates or entity in seen or
                row.get('level') != candidates[entity] or row['level'] not in LAYERS or
                not isinstance(refs, list) or any(ref not in evidence[entity] for ref in refs)):
            raise ValueError('Ranking contains a hallucinated entity or private evidence reference')
        reason = row.get('reason', '')
        if not isinstance(reason, str) or PRIVATE_TEXT.search(reason):
            raise ValueError('Ranking reason contains a private or invalid reference')
        seen.add(entity)
        cited += len(refs)
    if (prediction.get('root_layer'), prediction.get('root_entity')) != (
            ranking[0]['level'], ranking[0]['entity']):
        raise ValueError('Root verdict differs from ranking Top1')
    llm = prediction.get('llm_metadata', {})
    if (llm.get('transport') != 'https' or
            llm.get('endpoint_host') in (None, '', 'localhost', '127.0.0.1') or
            llm.get('model') in (None, '', 'synthetic-transport-stub')):
        raise ValueError('Prediction lacks real independent HTTPS endpoint metadata')
    if (audit.get('status') != 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_PASS' or
            audit.get('input_sha256') != sha256(bundle_file) or
            audit.get('prediction_sha256') != sha256(prediction_file) or
            audit.get('endpoint_host') != llm['endpoint_host'] or
            audit.get('public_read_allowed') is not True or
            audit.get('home_read_denied') is not True or
            audit.get('other_temp_read_denied') is not True or
            audit.get('gt_read') is not False):
        raise ValueError('Public/GT isolation audit is incomplete')
    return {'status': 'OPENRCA_XL_BLIND_OUTPUT_VALIDATED',
            'root_layer': ranking[0]['level'], 'root_entity': ranking[0]['entity'],
            'ranking_count': len(ranking), 'public_evidence_references': cited,
            'input_sha256': sha256(bundle_file),
            'prediction_sha256': sha256(prediction_file),
            'audit_sha256': sha256(audit_file),
            'schema': 'PASS', 'candidate_membership': 'PASS',
            'evidence_references': 'PASS', 'public_gt_isolation': 'PASS'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--prediction', required=True, type=Path)
    parser.add_argument('--audit', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite validation result')
    result = validate(args.input, args.prediction, args.audit)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
