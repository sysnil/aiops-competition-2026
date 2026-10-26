#!/usr/bin/env python3
"""Export only an admitted public six-file bundle from one immutable run."""

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen


OPS = Path('/root/ops-competition-2026')
EXPORTER = OPS / 'final-case-production-v2-20261020/scripts/public_export.py'
DATA = Path('/data/stage5-v3/ops-competition-2026')
PUBLIC_NAMES = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
                'trace_index.parquet', 'topology.json', 'manifest.json'}


def read(path):
    return json.loads(path.read_text())


def write_once(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def run(run_id):
    case = run_id.split('-ATTEMPT-', 1)[0]
    if (case not in {f'SF-{number:03d}' for number in range(13, 18)} or
            not run_id.startswith(case + '-ATTEMPT-')):
        raise ValueError('FINAL_CASE_RUN_ID_REQUIRED')
    source = DATA / 'runs' / run_id
    terminal = read(source / 'capture-terminal.json')
    quality = read(source / 'quality-audit.json')
    schedule = read(source / 'schedule.json')
    if (terminal['status'] != 'FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION' or
            quality['status'] != 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION' or
            not (source / 'control/action-results.json').is_file() or
            schedule['run_id'] != run_id or
            schedule['duration_seconds'] != 1800):
        raise RuntimeError('FORMAL_CAPTURE_OR_ADMISSION_NOT_ACCEPTED')
    export = DATA / 'exports' / run_id
    public = export / 'public'
    work = export / 'private-export-work'
    if export.exists():
        raise ValueError('FORMAL_EXPORT_ALREADY_EXISTS')
    export.mkdir(parents=True, mode=0o700)
    window = {'case_id': case, 'start_utc': schedule['start_utc'],
              'end_utc': schedule['end_utc']}
    write_once(export / 'private-public-window.json', window)
    with urlopen('http://10.97.14.138:9090/api/v1/metadata', timeout=30) as response:
        metadata = json.load(response)
    if metadata.get('status') != 'success' or not isinstance(
            metadata.get('data'), dict):
        raise RuntimeError('FORMAL_METRIC_METADATA_UNAVAILABLE')
    write_once(export / 'private-metric-metadata.json', metadata)
    command = ['/usr/bin/python3', '-B', str(EXPORTER),
               '--captures', str(source / 'capture'),
               '--window', str(export / 'private-public-window.json'),
               '--quality', str(source / 'quality-audit.json'),
               '--metric-metadata', str(export / 'private-metric-metadata.json'),
               '--output', str(public), '--work', str(work)]
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(OPS / 'export-runtime/cp312') + \
        (':' + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    proc = subprocess.run(command, capture_output=True, text=True,
                          timeout=1800, env=environment)
    write_once(export / 'private-export-command.json', {
        'argv': command, 'returncode': proc.returncode,
        'stdout_tail': proc.stdout[-4000:], 'stderr_tail': proc.stderr[-4000:]})
    if proc.returncode:
        raise RuntimeError('FORMAL_EXPORT_FAILED')
    if {path.name for path in public.iterdir()} != PUBLIC_NAMES:
        raise RuntimeError('PUBLIC_FILE_ALLOWLIST_MISMATCH')
    import hashlib
    manifest = read(public / 'manifest.json')
    for name, entry in manifest['files'].items():
        path = public / name
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry['sha256'] or path.stat().st_size != entry['bytes']:
            raise RuntimeError('PUBLIC_MANIFEST_FILE_HASH_MISMATCH:' + name)
    return {'status': 'PUBLIC_EXPORT_VERIFIED_PENDING_ADMISSION_AND_ARCHIVE',
            'run_id': run_id, 'public_path': str(public),
            'manifest_sha256': hashlib.sha256((public / 'manifest.json').read_bytes()).hexdigest(),
            'row_counts': manifest['row_counts'],
            'bytes': sum(path.stat().st_size for path in public.iterdir())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = run(args.run_id)
    export = DATA / 'exports' / args.run_id
    write_once(export / 'private-export-verified.json', result)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
