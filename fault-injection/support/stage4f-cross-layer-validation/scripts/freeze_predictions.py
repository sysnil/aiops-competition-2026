#!/usr/bin/env python3
"""Validate and freeze Stage4F predictions before the evaluator reads GT."""

import argparse
import json
from pathlib import Path

from contracts import sha256
from freeze_public import CASES, verify as verify_public


METHODS = ('OpenRCA-XL', 'MicroIRC-original', 'MicroIRC-XL',
           'CrossLayer-MicroRCA', 'CrossLayer-SimpleRCA')
NETWORK_A_METHODS = ('OpenRCA-XL', 'CrossLayer-MicroRCA', 'CrossLayer-SimpleRCA')


def expected_runs():
    for case in CASES:
        for method in METHODS:
            yield case, 'variant-b', method
        for method in NETWORK_A_METHODS:
            yield case, 'variant-a', method


def freeze(root):
    public = verify_public(root)
    output = root / 'prediction-freeze-manifest.json'
    if output.exists():
        raise ValueError('Predictions are already frozen')
    records = {}
    for case, variant, method in expected_runs():
        directory = root / 'runs' / case / variant / method
        prediction_file = directory / 'prediction.json'
        run_file = directory / 'run-manifest.json'
        if not prediction_file.is_file() or not run_file.is_file():
            raise ValueError('Missing run record: ' + str(directory))
        run = json.loads(run_file.read_text())
        prediction = json.loads(prediction_file.read_text())
        if (run.get('case_id'), run.get('variant'), run.get('algorithm')) != (case, variant[-1].upper(), method):
            raise ValueError('Run identity mismatch: ' + str(directory))
        bundle_name = f'cases/{case}/public/{variant}/bundle.json'
        if run.get('public_bundle_sha256') != public['files'][bundle_name]['sha256']:
            raise ValueError('Run public input hash mismatch: ' + str(directory))
        bundle = json.loads((root / bundle_name).read_text())
        universe = {item['entity'] for item in bundle['candidates']}
        if prediction.get('public_case_id') != bundle['public_case_id'] or 'case_id' in prediction:
            raise ValueError('Prediction contains a private case label or mismatched UUID')
        status = run.get('status')
        if status in ('PASS', 'NO_PREDICTION', 'PARTIAL_ADAPTATION'):
            if run.get('public_bundle_only') is not True or run.get('after_data_excluded') is not True:
                raise ValueError('Run input visibility contract failed: ' + str(directory))
            ranking = prediction.get('ranking')
            if not isinstance(ranking, list) or (status == 'PASS' and not ranking):
                raise ValueError('Successful run lacks ranking: ' + str(directory))
            if status == 'NO_PREDICTION' and ranking:
                raise ValueError('No-prediction run contains ranking: ' + str(directory))
            if status != 'PASS' and not run.get('reason'):
                raise ValueError('Non-PASS run requires reason: ' + str(directory))
            if [item.get('rank') for item in ranking] != list(range(1, len(ranking) + 1)):
                raise ValueError('Nonconsecutive ranks: ' + str(directory))
            if len({item.get('entity') for item in ranking}) != len(ranking):
                raise ValueError('Duplicate ranked entity: ' + str(directory))
            if any(item.get('entity') not in universe or not str(item.get('entity')).startswith(
                    str(item.get('level')) + '/') for item in ranking):
                raise ValueError('Ranking leaves the public typed candidate universe: ' + str(directory))
            if ranking and (prediction.get('root_layer'), prediction.get('root_entity')) != (
                    ranking[0].get('level'), ranking[0].get('entity')):
                raise ValueError('Top1 and verdict disagree: ' + str(directory))
            isolation = json.loads((directory / 'isolation-audit.json').read_text())
            if (isolation.get('evaluator_gt_read_denied') is not True or
                    isolation.get('after_data_read_denied') is not True or
                    isolation.get('stage_case_label_hidden') is not True or
                    isolation.get('design_read_denied') is not True):
                raise ValueError('Missing private or after-read isolation: ' + str(directory))
            for name in ('command.txt', 'stdout.log', 'stderr.log', 'runtime.json'):
                if not (directory / name).is_file():
                    raise ValueError('Missing reproducibility artifact: ' + str(directory / name))
        elif status == 'BLOCKED_PREREQUISITE':
            if prediction.get('ranking') or not run.get('reason'):
                raise ValueError('Blocked run has ranking or no reason: ' + str(directory))
        else:
            raise ValueError('Unknown run status: ' + str(status))
        for path in sorted(directory.iterdir()):
            if path.is_file():
                records[str(path.relative_to(root))] = {'sha256': sha256(path), 'bytes': path.stat().st_size}
    payload = {'status': 'STAGE4F_PREDICTIONS_FROZEN_BEFORE_GT',
               'public_freeze_sha256': sha256(root / 'public-freeze-manifest.json'),
               'files': dict(sorted(records.items()))}
    with output.open('x') as stream:
        json.dump(payload, stream, indent=2)
        stream.write('\n')
    output.chmod(0o444)
    return payload


def verify(root):
    manifest = json.loads((root / 'prediction-freeze-manifest.json').read_text())
    if manifest['status'] != 'STAGE4F_PREDICTIONS_FROZEN_BEFORE_GT':
        raise ValueError('Unknown prediction freeze status')
    if manifest['public_freeze_sha256'] != sha256(root / 'public-freeze-manifest.json'):
        raise ValueError('Public freeze changed')
    verify_public(root)
    current = {str(path.relative_to(root)) for case, variant, method in expected_runs()
               for path in (root / 'runs' / case / variant / method).iterdir() if path.is_file()}
    if current != set(manifest['files']):
        raise ValueError('Prediction artifact file set changed')
    for name, item in manifest['files'].items():
        path = root / name
        if sha256(path) != item['sha256'] or path.stat().st_size != item['bytes']:
            raise ValueError('Frozen prediction artifact changed: ' + name)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=('freeze', 'verify'))
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    result = freeze(args.root) if args.operation == 'freeze' else verify(args.root)
    print(json.dumps({'status': result['status'], 'files': len(result['files'])}))
