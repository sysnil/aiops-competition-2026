#!/usr/bin/env python3
"""Run OpenRCA-XL with only one public bundle visible to the child process."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse


RUNNER = Path(__file__).resolve().parents[1] / 'algorithms/openrca_xl_runner.py'
PYTHON = '/usr/bin/python3'
SANDBOX = '/usr/bin/sandbox-exec'


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_class(stderr):
    endpoint = re.search(r'Endpoint HTTP (\d{3}) code=([A-Za-z0-9_.-]*) '
                         r'param=([A-Za-z0-9_.-]*) category=([A-Za-z0-9_.-]+)', stderr)
    if endpoint:
        return {'kind': 'HTTP_ERROR', 'http_status': int(endpoint.group(1)),
                'error_code': endpoint.group(2) or None,
                'error_param': endpoint.group(3) or None,
                'error_category': endpoint.group(4)}
    http = re.search(r'HTTP Error (\d{3})', stderr)
    if http:
        return {'kind': 'HTTP_ERROR', 'http_status': int(http.group(1))}
    for token, kind in (
            ('PermissionError', 'SANDBOX_FILE_PERMISSION'),
            ('URLError', 'NETWORK_OR_TLS_ERROR'),
            ('TimeoutError', 'ENDPOINT_TIMEOUT'),
            ('JSONDecodeError', 'ENDPOINT_JSON_ERROR'),
            ('KeyError', 'ENDPOINT_SCHEMA_ERROR'),
            ('ValueError', 'ENDPOINT_OUTPUT_VALIDATION_ERROR')):
        if token in stderr:
            return {'kind': kind, 'http_status': None}
    return {'kind': 'ISOLATED_PROCESS_ERROR', 'http_status': None}


def profile_for(directory):
    path = str(directory)
    if not path.startswith('/private/tmp/stage4f-openrca-public.'):
        raise ValueError('Public runtime must be a private temporary directory')
    return ('(version 1) (allow default) '
            '(deny file-read* (subpath "/Users")) '
            '(deny file-write* (subpath "/Users")) '
            '(deny file-read* (subpath "/Volumes")) '
            '(deny file-read* (subpath "/private/tmp")) '
            '(deny file-write* (subpath "/private/tmp")) '
            f'(allow file-read* (subpath "{path}")) '
            f'(allow file-write* (subpath "{path}"))')


def can_read(profile, env, target, directory):
    command = [SANDBOX, '-p', profile, PYTHON, '-S', '-c',
               'from pathlib import Path; Path(__import__("sys").argv[1]).read_bytes()',
               str(target)]
    probe = subprocess.run(command, cwd=directory, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
    return probe.returncode == 0


def prediction_outcome(prediction, input_hash, endpoint_host):
    ranking = prediction.get('ranking')
    metadata = prediction.get('llm_metadata', {})
    if (prediction.get('input_sha256') != input_hash or
            metadata.get('transport') != 'https' or
            metadata.get('endpoint_host') != endpoint_host or
            not isinstance(ranking, list)):
        raise ValueError('Real endpoint prediction identity or transport is invalid')
    if (prediction.get('status') == 'PASS' and ranking and
            (prediction.get('root_entity'), prediction.get('root_layer')) ==
            (ranking[0].get('entity'), ranking[0].get('level'))):
        return 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_PASS'
    if (prediction.get('status') == 'NO_PREDICTION' and not ranking and
            prediction.get('root_entity') is None and prediction.get('root_layer') is None):
        return 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_VALID_ABSTENTION'
    raise ValueError('Real endpoint response did not satisfy the blind output contract')


def run(bundle, output, audit_output):
    if output.exists() or audit_output.exists():
        raise ValueError('Refusing to overwrite a blind smoke artifact')
    endpoint = os.environ.get('OPENRCA_XL_ENDPOINT_URL', '')
    model = os.environ.get('OPENRCA_XL_MODEL', '')
    token = os.environ.get('OPENRCA_XL_API_KEY', '')
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != 'https' or not parsed.hostname or not model or not token:
        raise ValueError('Independent HTTPS endpoint, model and API key are required')
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError('Endpoint URL must not contain credentials or a fragment')
    original_hash = sha256(bundle)
    with tempfile.TemporaryDirectory(prefix='stage4f-openrca-public.', dir='/private/tmp') as temp:
        directory = Path(temp)
        public_file = directory / 'bundle.json'
        runner_file = directory / 'runner.py'
        prediction_file = directory / 'prediction.json'
        shutil.copyfile(bundle, public_file)
        shutil.copyfile(RUNNER, runner_file)
        if sha256(public_file) != original_hash:
            raise ValueError('Public input changed during staging')
        profile = profile_for(directory)
        env = {'PATH': '/usr/bin:/bin', 'HOME': temp, 'TMPDIR': temp,
               'PYTHONNOUSERSITE': '1', 'PYTHONPATH': '',
               'PYTHONDONTWRITEBYTECODE': '1',
               'OPENRCA_XL_ENDPOINT_URL': endpoint,
               'OPENRCA_XL_MODEL': model,
               'OPENRCA_XL_API_KEY': token}
        public_read_allowed = can_read(profile, env, public_file, directory)
        home_denied = not can_read(profile, env, RUNNER, directory)
        with tempfile.NamedTemporaryFile(prefix='stage4f-isolation-sentinel.',
                                         dir='/private/tmp') as sentinel:
            other_tmp_denied = not can_read(profile, env, sentinel.name, directory)
        if not public_read_allowed or not home_denied or not other_tmp_denied:
            raise ValueError('Public-input sandbox file boundary failed')
        command = [SANDBOX, '-p', profile, PYTHON, '-S', str(runner_file),
                   '--input', str(public_file), '--output', str(prediction_file)]
        completed = subprocess.run(command, cwd=directory, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, timeout=180)
        if completed.returncode != 0 or not prediction_file.is_file():
            failure = failure_class(completed.stderr)
            failure_audit = {'status': 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_FAILED',
                             'at_utc': datetime.now(timezone.utc).isoformat(),
                             'endpoint_host': parsed.hostname, 'model': model,
                             'input_sha256': original_hash,
                             'runner_sha256': sha256(RUNNER),
                             'sandbox_profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
                             'public_read_allowed': public_read_allowed,
                             'home_read_denied': home_denied,
                             'other_temp_read_denied': other_tmp_denied,
                             'failure': failure}
            audit_output.parent.mkdir(parents=True, exist_ok=True)
            audit_output.write_text(json.dumps(failure_audit, indent=2) + '\n')
            raise RuntimeError('Isolated OpenRCA-XL run failed: ' + failure['kind'] +
                               (f" HTTP {failure['http_status']}" if failure['http_status'] else ''))
        prediction = json.loads(prediction_file.read_text())
        metadata = prediction.get('llm_metadata', {})
        ranking = prediction.get('ranking')
        checks = {'nonempty_ranking': isinstance(ranking, list) and bool(ranking),
                  'pass_status': prediction.get('status') == 'PASS',
                  'top1_consistent': bool(ranking) and
                  (prediction.get('root_entity'), prediction.get('root_layer')) ==
                  (ranking[0].get('entity'), ranking[0].get('level')),
                  'input_hash_match': prediction.get('input_sha256') == original_hash,
                  'https_transport': metadata.get('transport') == 'https',
                  'endpoint_host_match': metadata.get('endpoint_host') == parsed.hostname}
        try:
            outcome = prediction_outcome(prediction, original_hash, parsed.hostname)
        except ValueError:
            audit_output.parent.mkdir(parents=True, exist_ok=True)
            audit_output.write_text(json.dumps({
                'status': 'OPENRCA_XL_REAL_HTTPS_BLIND_SMOKE_FAILED',
                'at_utc': datetime.now(timezone.utc).isoformat(),
                'endpoint_host': parsed.hostname, 'model': model,
                'input_sha256': original_hash, 'runner_sha256': sha256(RUNNER),
                'public_read_allowed': public_read_allowed,
                'home_read_denied': home_denied,
                'other_temp_read_denied': other_tmp_denied,
                'model_reported_status': prediction.get('status'),
                'ranking_count': len(ranking) if isinstance(ranking, list) else None,
                'checks': checks}, indent=2) + '\n')
            raise ValueError('Real endpoint response did not satisfy the blind output contract')
        output.parent.mkdir(parents=True, exist_ok=True)
        audit_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(prediction_file, output)
        audit = {'status': outcome,
                 'at_utc': datetime.now(timezone.utc).isoformat(),
                 'endpoint_host': parsed.hostname, 'model': metadata.get('model'),
                 'input_sha256': original_hash, 'prediction_sha256': sha256(output),
                 'runner_sha256': sha256(RUNNER),
                 'sandbox_profile_sha256': hashlib.sha256(profile.encode()).hexdigest(),
                 'public_read_allowed': public_read_allowed,
                 'home_read_denied': home_denied,
                 'other_temp_read_denied': other_tmp_denied,
                 'public_only': True, 'gt_read': False,
                 'ranking_count': len(prediction['ranking'])}
        audit_output.write_text(json.dumps(audit, indent=2) + '\n')
        return audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--audit-output', type=Path, required=True)
    args = parser.parse_args()
    audit = run(args.input, args.output, args.audit_output)
    print(json.dumps({'status': audit['status'], 'endpoint_host': audit['endpoint_host'],
                      'model': audit['model'], 'ranking_count': audit['ranking_count']}))


if __name__ == '__main__':
    main()
