#!/usr/bin/env python3
"""Freeze Stage4F public bundles after conversion and before RCA inference."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import uuid

from contracts import verify as verify_contracts, sha256
from freeze_preflight import verify as verify_preflight


CASES = ('F01', 'F02', 'F03', 'F04')
COMMON = ('bundle.json', 'case-quality.json', 'service-topology.json', 'pod-placement.json',
          'node-placement.json', 'metrics', 'logs', 'traces', 'kubernetes')
NETWORK = ('network-paths.json', 'edge-path-map.json', 'normal-network-observations.json')
FORBIDDEN = re.compile(
    rb'(?i)(?:\bVM1\b|gretap|netem|fault[_ -]?target|fault[_ -]?parameter|'
    rb'injection[_ -]?command|root[_ -]?layer|root[_ -]?entity|evaluator_gt|\bF0[1-8]\b)'
)
TEXT_SUFFIXES = {'.json', '.jsonl', '.yaml', '.yml', '.csv', '.txt', '.md'}


def private_token(path):
    overlap = b''
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            data = overlap + block
            found = FORBIDDEN.search(data)
            if found:
                return found.group().decode('ascii', errors='replace')
            overlap = data[-64:]
    return None


def freeze(root):
    verify_contracts(root)
    for case in CASES:
        verify_preflight(root, case)
    output = root / 'public-freeze-manifest.json'
    if output.exists():
        raise ValueError('Public inputs already frozen')
    records = {}
    opaque_ids = {}
    for case in CASES:
        for variant in ('variant-a', 'variant-b'):
            directory = root / 'cases' / case / 'public' / variant
            if not directory.is_dir():
                raise ValueError('Missing public variant: ' + str(directory))
            required = COMMON + (NETWORK if variant == 'variant-b' else ())
            for name in required:
                if not (directory / name).exists():
                    raise ValueError('Missing public input: ' + str(directory / name))
            for name in ('metrics', 'logs', 'traces', 'kubernetes'):
                if not any((directory / name).rglob('*')):
                    raise ValueError('Empty public telemetry or metadata directory: ' + str(directory / name))
            if variant == 'variant-a' and any((directory / name).exists() for name in NETWORK):
                raise ValueError('Variant A contains network path context')
            bundle = json.loads((directory / 'bundle.json').read_text())
            if (bundle.get('schema_version') != 1 or bundle.get('public_only') is not True or
                    'case_id' in bundle or bundle.get('variant') != variant[-1].upper()):
                raise ValueError('Public bundle identity mismatch: ' + str(directory))
            quality = json.loads((directory / 'case-quality.json').read_text())
            if set(quality) != {'before_seconds', 'during_seconds',
                                'before_during_comparable', 'observability_pass',
                                'public_private_leak_scan_pass'}:
                raise ValueError('Public case quality contains target or after evidence')
            try:
                opaque = uuid.UUID(bundle['public_case_id'])
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError('Missing opaque public case UUID') from exc
            if opaque.version != 4 or (case in opaque_ids and opaque_ids[case] != str(opaque)) or (
                    case not in opaque_ids and str(opaque) in opaque_ids.values()):
                raise ValueError('Public case UUID is reused or inconsistent')
            opaque_ids[case] = str(opaque)
            preflight = root / 'preflight' / case
            candidate_record = json.loads((preflight / 'candidate-universe.json').read_text())
            if bundle['public_case_id'] != candidate_record['public_case_id']:
                raise ValueError('Public case UUID differs from pre-fault freeze: ' + case)
            candidates = candidate_record['candidates']
            candidate_ids = {item['entity'] for item in candidates}
            if variant == 'variant-a':
                candidate_ids = {item for item in candidate_ids if not item.startswith('NETWORK_PATH/')}
            if {item['entity'] for item in bundle.get('candidates', [])} != candidate_ids:
                raise ValueError('Public candidates differ from pre-fault frozen universe: ' + case)
            frozen_relations = [item for item in candidate_record.get('relations', [])
                                if variant == 'variant-b' or item['type'] != 'TRAVERSES']
            if bundle.get('relations') != frozen_relations:
                raise ValueError('Public topology differs from pre-fault frozen universe: ' + case)
            null = json.loads((preflight / 'clean-null-calibration.json').read_text())
            frozen_null = null.get('variants', {}).get(variant[-1].upper(), {})
            if (bundle.get('clean_null_window_maxima') != frozen_null.get('clean_null_window_maxima') or
                    bundle.get('clean_null_pagerank_maxima') !=
                    frozen_null.get('clean_null_pagerank_maxima')):
                raise ValueError('Clean-null calibration changed after the fault: ' + case)
            if case == 'F04' and variant == 'variant-b':
                precheck = json.loads((preflight / 'network-path-precheck.json').read_text())
                mapping = json.loads((directory / 'edge-path-map.json').read_text())
                if (set(mapping.get('traversing_edges', [])) != set(precheck['traversing_edges']) or
                        set(mapping.get('control_edges', [])) != set(precheck['control_edges'])):
                    raise ValueError('Public path-edge mapping changed after pre-fault freeze')
            entries = list(directory.rglob('*'))
            if any(path.is_symlink() for path in entries):
                raise ValueError('Symlink in public bundle: ' + str(directory))
            files = [path for path in entries if path.is_file()]
            if not files:
                raise ValueError('Empty public variant: ' + str(directory))
            for path in files:
                if path.suffix.lower() in TEXT_SUFFIXES:
                    token = private_token(path)
                    if token:
                        raise ValueError(f'Private token {token} in {path}')
                records[str(path.relative_to(root))] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
    payload = {'status': 'STAGE4F_PUBLIC_BUNDLES_FROZEN_BEFORE_INFERENCE',
               'contract_freeze_sha256': sha256(root / 'freeze-manifest.json'),
               'preflight_freeze_sha256': {case: sha256(root / 'preflight' / case / 'freeze-manifest.json')
                                           for case in CASES},
               'files': dict(sorted(records.items()))}
    with output.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    output.chmod(0o444)
    return payload


def verify(root):
    manifest = json.loads((root / 'public-freeze-manifest.json').read_text())
    if manifest['status'] != 'STAGE4F_PUBLIC_BUNDLES_FROZEN_BEFORE_INFERENCE':
        raise ValueError('Unknown public freeze status')
    if manifest['contract_freeze_sha256'] != sha256(root / 'freeze-manifest.json'):
        raise ValueError('Contract freeze changed')
    verify_contracts(root)
    for case, expected in manifest['preflight_freeze_sha256'].items():
        if sha256(root / 'preflight' / case / 'freeze-manifest.json') != expected:
            raise ValueError('Preflight freeze changed: ' + case)
        verify_preflight(root, case)
    current = {str(path.relative_to(root)) for case in CASES for path in
               (root / 'cases' / case / 'public').rglob('*') if path.is_file()}
    if current != set(manifest['files']):
        raise ValueError('Public artifact file set changed')
    for name, item in manifest['files'].items():
        path = root / name
        if sha256(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Public artifact changed: ' + name)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('freeze', 'verify'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result = freeze(args.root) if args.operation == 'freeze' else verify(args.root)
    print(json.dumps({'status': result['status'], 'files': len(result['files'])}))
