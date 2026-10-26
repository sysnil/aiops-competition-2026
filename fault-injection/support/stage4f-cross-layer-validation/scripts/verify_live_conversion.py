#!/usr/bin/env python3
"""Independently audit one unfrozen, GT-free Stage4F live conversion."""

import argparse
import json
from pathlib import Path

from contracts import sha256
from freeze_public import TEXT_SUFFIXES, private_token
from network_path_precheck import validate as validate_network_path
from pod_precheck import FAMILIES


PRIVATE_KEYS = {'case_id', 'stage_case_id', 'experiment_id', 'evaluator_gt',
                'fault_type', 'fault_target', 'fault_parameter',
                'injection_command', 'root_layer', 'root_entity'}


def check_private_keys(value):
    if isinstance(value, dict):
        if PRIVATE_KEYS.intersection(value):
            raise ValueError('Private key in public artifact')
        for child in value.values():
            check_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            check_private_keys(child)


def verify(output, before, during, network_proof=None, network_probe=None):
    manifest_path = output / 'conversion-manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('status') != 'STAGE4F_LIVE_CONVERSION_COMPLETE' or
            manifest.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE'):
        raise ValueError('Unknown live conversion status')
    files = {str(file.relative_to(output)): file for file in output.rglob('*')
             if file.is_file() and file != manifest_path}
    if set(files) != set(manifest['output_sha256']):
        raise ValueError('Live conversion output file set changed')
    for name, file in files.items():
        if sha256(file) != manifest['output_sha256'][name]:
            raise ValueError('Live conversion SHA-256 mismatch: ' + name)
    for phase, directory in (('before', before), ('during', during)):
        capture = json.loads((directory / 'capture-manifest.json').read_text())
        if (sha256(directory / 'capture-manifest.json') != manifest['input_capture_sha256'][phase] or
                capture.get('trace_source') != 'JAEGER_OPENSEARCH_STORAGE'):
            raise ValueError('Input capture manifest changed: ' + phase)
    if network_proof:
        proof = json.loads(network_proof.read_text())
        validate_network_path(proof)
        from hashlib import sha256 as hash_bytes
        expected = hash_bytes(json.dumps(proof, sort_keys=True).encode()).hexdigest()
        if expected != manifest['network_proof_sha256']:
            raise ValueError('Verified public route map changed')
    else:
        proof = None
        if manifest.get('network_proof_sha256'):
            raise ValueError('Network proof missing from audit')
    if network_probe:
        if sha256(network_probe) != manifest['network_probe_sha256']:
            raise ValueError('Network probe changed')
    elif manifest.get('network_probe_sha256'):
        raise ValueError('Network probe missing from audit')
    public = output / 'public'
    for file in public.rglob('*'):
        if file.is_file() and file.suffix.lower() in TEXT_SUFFIXES:
            token = private_token(file)
            if token:
                raise ValueError('Private token in public artifact: ' + str(file))
            if file.suffix.lower() == '.json':
                check_private_keys(json.loads(file.read_text()))
    candidate_record = json.loads((output / 'candidate-universe.json').read_text())
    candidate_ids = {item['entity'] for item in candidate_record['candidates']}
    bundles = {variant: json.loads((public / ('variant-' + variant.lower()) /
                                    'bundle.json').read_text()) for variant in ('A', 'B')}
    for variant, bundle in bundles.items():
        quality = json.loads((public / ('variant-' + variant.lower()) /
                              'case-quality.json').read_text())
        if set(quality) != {'before_seconds', 'during_seconds',
                            'before_during_comparable', 'observability_pass',
                            'public_private_leak_scan_pass'}:
            raise ValueError('Public case quality exposes private or after evidence')
        if (bundle.get('schema_version') != 1 or bundle.get('public_only') is not True or
                bundle.get('variant') != variant or 'case_id' in bundle or
                bundle.get('public_case_id') != candidate_record['public_case_id']):
            raise ValueError('Anonymous public bundle identity mismatch')
        expected = candidate_ids if variant == 'B' else {
            entity for entity in candidate_ids if not entity.startswith('NETWORK_PATH/')}
        if {item['entity'] for item in bundle['candidates']} != expected:
            raise ValueError('Public candidate universe differs from pre-fault metadata')
        if any(phase not in ('before', 'during') for modality in bundle['observations'].values()
               for rows in modality.values() for item in rows
               if isinstance(item, dict) for phase in [item.get('phase', 'before')]):
            raise ValueError('After or unknown observation is algorithm-visible')
        for layer in {item['layer'] for item in bundle['candidates']}:
            if (len(bundle['clean_null_window_maxima'].get(layer, [])) < 8 or
                    len(bundle['clean_null_pagerank_maxima'].get(layer, [])) < 8):
                raise ValueError('Missing independent clean calibration for ' + layer)
    a, b = bundles['A'], bundles['B']
    if ('network_context' in a or any(item['type'] == 'TRAVERSES'
                                      for item in a['relations']) or
            any(item['layer'] == 'NETWORK_PATH' for item in a['candidates'])):
        raise ValueError('Variant A leaks NetworkPath context')
    if proof:
        if (b.get('network_context') != proof or
                sum(item['type'] == 'TRAVERSES' for item in b['relations']) !=
                len(validate_network_path(proof)['traversing_edges'])):
            raise ValueError('Variant B omits verified edge-path membership')
    coverage_file = output / 'canonical/target-pod-coverage.json'
    if coverage_file.is_file():
        coverage = json.loads(coverage_file.read_text())
        for phase, minimum in (('before', 20), ('during', 10)):
            if any(coverage['sample_counts'][phase].get(name, 0) < minimum for name in FAMILIES):
                raise ValueError('F02 target five-series coverage is incomplete')
    return {'status': 'STAGE4F_LIVE_CONVERSION_VERIFIED',
            'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
            'public_case_id': a['public_case_id'], 'files': len(files),
            'candidate_counts': {'A': len(a['candidates']), 'B': len(b['candidates'])},
            'network_path_proof': bool(proof),
            'f02_target_coverage': coverage_file.is_file(),
            'public_private_leak_scan': 'PASS'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--during', required=True, type=Path)
    parser.add_argument('--network-proof', type=Path)
    parser.add_argument('--network-probe', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.before, args.during,
                            args.network_proof, args.network_probe)))


if __name__ == '__main__':
    main()
