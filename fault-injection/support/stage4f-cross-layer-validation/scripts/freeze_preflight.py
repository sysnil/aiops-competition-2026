#!/usr/bin/env python3
"""Freeze one case's clean candidate, route and calibration inputs before its fault."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from contracts import sha256, verify as verify_contracts
from network_path_precheck import validate as validate_network_path
from pod_precheck import validate as validate_pod
from service_node_precheck import node as validate_node, service as validate_service


PRECHECK_FILE = {
    'F01': ('service-precheck.json', 'SERVICE_CANDIDATE_PRECHECK_PASS'),
    'F02': ('pod-precheck.json', 'POD_CANDIDATE_PRECHECK_PASS'),
    'F03': ('node-precheck.json', 'NODE_CANDIDATE_PRECHECK_PASS'),
    'F04': ('network-path-precheck.json', 'NETWORK_PATH_PRECHECK_PASS'),
}
REQUIRED = ('candidate-universe.json', 'clean-null-calibration.json', 'public-snapshot.json')
VALIDATORS = {'F01': validate_service, 'F02': validate_pod,
              'F03': validate_node, 'F04': validate_network_path}


def freeze(root, case):
    verify_contracts(root)
    directory = root / 'preflight' / case
    if case not in PRECHECK_FILE or not directory.is_dir():
        raise ValueError('Unknown or missing case preflight')
    manifest = directory / 'freeze-manifest.json'
    if manifest.exists():
        raise ValueError('Case preflight already frozen')
    if (root / 'cases' / case / 'fault').exists() or (root / 'evaluator_gt' / case).exists():
        raise ValueError('Fault or GT already exists before preflight freeze')
    precheck_file, expected_status = PRECHECK_FILE[case]
    names = REQUIRED + (precheck_file,)
    for name in names:
        if not (directory / name).is_file():
            raise ValueError('Missing preflight evidence: ' + name)
    precheck = json.loads((directory / precheck_file).read_text())
    if precheck.get('status') != expected_status:
        raise ValueError('Preflight gate did not pass')
    snapshot_file = directory / 'public-snapshot.json'
    computed = VALIDATORS[case](json.loads(snapshot_file.read_text()))
    if any(precheck.get(key) != value for key, value in computed.items()):
        raise ValueError('Stored precheck differs from public source evidence')
    if precheck.get('input_sha256') != sha256(snapshot_file):
        raise ValueError('Precheck source hash mismatch')
    candidates = json.loads((directory / 'candidate-universe.json').read_text())
    if (candidates.get('public_only') is not True or candidates.get('case_id') != case or
            not candidates.get('candidates')):
        raise ValueError('Missing public pre-fault candidates')
    try:
        if uuid.UUID(candidates['public_case_id']).version != 4:
            raise ValueError('Public case ID must be UUIDv4')
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing public case UUID in preflight candidates') from exc
    candidate_ids = {item['entity'] for item in candidates['candidates']}
    precheck_candidate = computed.get('candidate') or computed.get('network_path_entity')
    if precheck_candidate not in candidate_ids:
        raise ValueError('Prechecked root candidate absent from frozen public universe')
    null = json.loads((directory / 'clean-null-calibration.json').read_text())
    if null.get('public_only') is not True or null.get('case_id') != case:
        raise ValueError('Missing public clean-null calibration')
    for variant in ('A', 'B'):
        variant_null = null.get('variants', {}).get(variant, {})
        layers = {item['layer'] for item in candidates['candidates']
                  if variant == 'B' or item['layer'] != 'NETWORK_PATH'}
        if set(variant_null.get('clean_null_window_maxima', {})) != layers or (
                set(variant_null.get('clean_null_pagerank_maxima', {})) != layers):
            raise ValueError('Clean-null candidate layers differ for Variant ' + variant)
        for layer in layers:
            simple = variant_null['clean_null_window_maxima'][layer]
            graph = variant_null['clean_null_pagerank_maxima'][layer]
            if (len(simple) < 8 or len(graph) < 8 or
                    any(not isinstance(value, (int, float)) or not 0 <= value <= 1 for value in simple) or
                    any(not isinstance(value, (int, float)) or value < 0 for value in graph)):
                raise ValueError('Insufficient clean-null calibration for ' + variant + '/' + layer)
    files = [path for path in directory.rglob('*') if path.is_file()]
    payload = {'status': 'STAGE4F_CASE_PREFLIGHT_FROZEN_BEFORE_FAULT', 'case_id': case,
               'frozen_at_utc': datetime.now(timezone.utc).isoformat(),
               'contract_freeze_sha256': sha256(root / 'freeze-manifest.json'),
               'files': {str(path.relative_to(directory)): sha256(path) for path in sorted(files)}}
    with manifest.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    manifest.chmod(0o444)
    return payload


def verify(root, case):
    verify_contracts(root)
    directory = root / 'preflight' / case
    manifest = json.loads((directory / 'freeze-manifest.json').read_text())
    if manifest['status'] != 'STAGE4F_CASE_PREFLIGHT_FROZEN_BEFORE_FAULT':
        raise ValueError('Unknown preflight freeze status')
    if manifest['contract_freeze_sha256'] != sha256(root / 'freeze-manifest.json'):
        raise ValueError('Contract freeze changed')
    current = {str(path.relative_to(directory)) for path in directory.rglob('*')
               if path.is_file() and path.name != 'freeze-manifest.json'}
    if current != set(manifest['files']):
        raise ValueError('Pre-fault evidence file set changed')
    for name, expected in manifest['files'].items():
        if sha256(directory / name) != expected:
            raise ValueError('Pre-fault evidence changed: ' + name)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('freeze', 'verify'))
    parser.add_argument('root', type=Path)
    parser.add_argument('case_id', choices=tuple(PRECHECK_FILE))
    args = parser.parse_args()
    result = freeze(args.root, args.case_id) if args.operation == 'freeze' else verify(args.root, args.case_id)
    print(json.dumps({'status': result['status'], 'case_id': result['case_id'],
                      'files': len(result['files'])}))
