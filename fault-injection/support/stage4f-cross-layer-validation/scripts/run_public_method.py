#!/usr/bin/env python3
"""Archive one anonymous Stage4F public-only inference run on Mac."""

import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import uuid

from launch_openrca_from_tty import chat_endpoint


ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT.parent / 'rca-eval/environments/microrca/bin/python'
CASE_LABEL = re.compile(r'(?i)(?:^|[/_-])F0[1-4](?:[/_.-]|$)')
METHODS = {'OpenRCA-XL': None,
           'CrossLayer-MicroRCA': ROOT / 'algorithms/microrca_xl.py',
           'CrossLayer-SimpleRCA': ROOT / 'algorithms/simple_rca_calibrated.py',
           'MicroIRC-original': ROOT / 'algorithms/microirc_stage4f_runner.py',
           'MicroIRC-XL': ROOT / 'algorithms/microirc_stage4f_runner.py'}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_bundle(path, variant):
    if CASE_LABEL.search(str(path)):
        raise ValueError('Algorithm-visible path contains a private F01-F04 label')
    bundle = json.loads(path.read_text())
    if (bundle.get('schema_version') != 1 or bundle.get('public_only') is not True or
            bundle.get('variant') != variant or 'case_id' in bundle or
            uuid.UUID(bundle['public_case_id']).version != 4 or
            any(phase not in ('before', 'during') for phase in bundle['windows'])):
        raise ValueError('Input is not an anonymous before/during-only public bundle')
    return bundle


def adapted_profile():
    private = (ROOT / 'design', ROOT / 'reports', ROOT / 'evaluator_gt',
               ROOT.parent / 'stage4e-baseline-probing', ROOT.parent / 'cross-layer-baselines')
    return ('(version 1) (allow default) (deny network-outbound) ' +
            ' '.join('(deny file-read* (subpath "' + str(path) + '"))' for path in private))


def adapted_isolation_pass(profile):
    forbidden = ROOT / 'design/candidate-contract.yaml'
    probe = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile,
                            str(VENV_PYTHON), '-c',
                            'from pathlib import Path; Path(__import__("sys").argv[1]).read_text()',
                            str(forbidden)], capture_output=True, timeout=20)
    return probe.returncode != 0


def status_from_prediction(prediction):
    status = prediction.get('status')
    if status == 'PASS' and prediction.get('ranking'):
        return 'PASS', None
    if status == 'PARTIAL_ADAPTATION':
        return status, 'Author-step coverage or correlation is incomplete'
    if status == 'BLOCKED_PREREQUISITE':
        return status, prediction.get('reason', 'Author inference prerequisite missing')
    if status in ('NO_ANOMALOUS_INVOCATION', 'NO_ELIGIBLE_CANDIDATE', 'NO_PREDICTION'):
        return 'NO_PREDICTION', status
    raise ValueError('Unknown or inconsistent author-method output status')


def run(case_id, variant, method, bundle_file, output, endpoint_base=None, model=None):
    if case_id not in ('F01', 'F02', 'F03', 'F04') or method not in METHODS or (
            variant not in ('A', 'B') or output.exists()):
        raise ValueError('Invalid or already archived algorithm-case run')
    if variant == 'A' and method.startswith('MicroIRC'):
        raise ValueError('MicroIRC is a nonmandatory Variant B-only capability audit')
    if CASE_LABEL.search(str(output)):
        raise ValueError('Algorithm output path contains a private F01-F04 label')
    bundle = validate_bundle(bundle_file, variant)
    candidates = {item['entity'] for item in bundle['candidates']}
    input_hash = sha256(bundle_file)
    output.mkdir(parents=True)
    prediction_file = output / 'prediction.json'
    start = datetime.now(timezone.utc)
    clock = time.monotonic()
    secret = None
    if method == 'OpenRCA-XL':
        if not endpoint_base or not model:
            raise ValueError('Real OpenRCA-XL endpoint and model are required')
        endpoint = chat_endpoint(endpoint_base)
        secret = getpass.getpass('Endpoint API key: ')
        if not secret:
            raise ValueError('Endpoint API key is empty')
        env = {**os.environ, 'OPENRCA_XL_ENDPOINT_URL': endpoint,
               'OPENRCA_XL_MODEL': model, 'OPENRCA_XL_API_KEY': secret}
        command = [sys.executable, str(ROOT / 'scripts/run_openrca_isolated.py'),
                   '--input', str(bundle_file), '--output', str(prediction_file),
                   '--audit-output', str(output / 'endpoint-audit.json')]
        isolation = {'evaluator_gt_read_denied': True, 'after_data_read_denied': True,
                     'stage_case_label_hidden': True, 'design_read_denied': True,
                     'mechanism': 'macos_sandbox_public_input_only'}
    else:
        env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
        profile = adapted_profile()
        if not adapted_isolation_pass(profile):
            raise ValueError('Adapted-method design-path isolation probe failed')
        command = ['/usr/bin/sandbox-exec', '-p', profile, str(VENV_PYTHON),
                   str(METHODS[method])]
        if method.startswith('MicroIRC'):
            command.extend(['--mode', 'original' if method.endswith('original') else 'xl'])
        command.extend(['--input', str(bundle_file), '--output', str(prediction_file)])
        isolation = {'evaluator_gt_read_denied': True, 'after_data_read_denied': True,
                     'stage_case_label_hidden': True, 'design_read_denied': True,
                     'mechanism': 'macos_sandbox_design_and_network_denied'}
    try:
        try:
            completed = subprocess.run(command, env=env, capture_output=True,
                                       text=True, timeout=240)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode(errors='replace') if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            stderr = exc.stderr.decode(errors='replace') if isinstance(exc.stderr, bytes) else (exc.stderr or '')
            completed = subprocess.CompletedProcess(command, 124, stdout, stderr + '\nINFERENCE_TIMEOUT\n')
    finally:
        if method == 'OpenRCA-XL':
            env.pop('OPENRCA_XL_API_KEY', None)
    runtime = time.monotonic() - clock
    stdout = completed.stdout.replace(secret, '[REDACTED]') if method == 'OpenRCA-XL' else completed.stdout
    stderr = completed.stderr.replace(secret, '[REDACTED]') if method == 'OpenRCA-XL' else completed.stderr
    (output / 'command.txt').write_text(shlex.join(command) + '\n')
    (output / 'stdout.log').write_text(stdout)
    (output / 'stderr.log').write_text(stderr)
    (output / 'runtime.json').write_text(json.dumps({
        'start_utc': start.isoformat(), 'end_utc': datetime.now(timezone.utc).isoformat(),
        'runtime_seconds': runtime, 'exit_code': completed.returncode}, indent=2) + '\n')
    if completed.returncode == 0 and prediction_file.is_file():
        prediction = json.loads(prediction_file.read_text())
        status, reason = status_from_prediction(prediction)
    else:
        status, reason = 'NO_PREDICTION', 'INFERENCE_FAILURE_EXIT_' + str(completed.returncode)
        prediction = {'algorithm': method, 'public_case_id': bundle['public_case_id'],
                      'variant': variant, 'status': status, 'reason': reason,
                      'root_layer': None, 'root_entity': None, 'ranking': [],
                      'input_sha256': input_hash}
        prediction_file.write_text(json.dumps(prediction, indent=2) + '\n')
    if prediction.get('public_case_id') != bundle['public_case_id'] or (
            prediction.get('input_sha256') not in (None, input_hash)):
        raise ValueError('Method prediction does not match the anonymous public input')
    ranking = prediction.get('ranking') or []
    if any(row.get('entity') not in candidates for row in ranking):
        raise ValueError('Prediction leaves the public candidate universe')
    if method == 'OpenRCA-XL' and status == 'PASS':
        audit = json.loads((output / 'endpoint-audit.json').read_text())
        if audit.get('gt_read') is not False or not audit.get('home_read_denied'):
            raise ValueError('OpenRCA-XL isolation audit failed')
    (output / 'isolation-audit.json').write_text(json.dumps(isolation, indent=2) + '\n')
    network_consumed = (variant == 'B' and bool(bundle.get('network_context')) and
                        method in ('OpenRCA-XL', 'CrossLayer-MicroRCA',
                                   'CrossLayer-SimpleRCA') and status == 'PASS')
    manifest = {'case_id': case_id, 'variant': variant, 'algorithm': method,
                'public_case_id': bundle['public_case_id'],
                'public_bundle_sha256': input_hash, 'status': status,
                'reason': reason, 'public_bundle_only': True,
                'after_data_excluded': True,
                'network_context_consumed': network_consumed,
                'runtime_seconds': runtime,
                'prediction_sha256': sha256(prediction_file)}
    (output / 'run-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return {'status': status, 'algorithm': method, 'variant': variant,
            'ranking_count': len(ranking), 'runtime_seconds': runtime}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-id', choices=('F01', 'F02', 'F03', 'F04'), required=True)
    parser.add_argument('--variant', choices=('A', 'B'), required=True)
    parser.add_argument('--method', choices=tuple(METHODS), required=True)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--endpoint-base')
    parser.add_argument('--model')
    args = parser.parse_args()
    print(json.dumps(run(args.case_id, args.variant, args.method, args.input,
                         args.output, args.endpoint_base, args.model)))


if __name__ == '__main__':
    main()
