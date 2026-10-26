#!/usr/bin/env python3
"""Exercise the final exporter on immutable no-fault data, never a Case."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
sys.path.insert(0, str(OPS / 'final-case-observation-v2-20261020/scripts'))
from assess_collection import assess, collect


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if (not args.run.name.startswith('SF-017-ATTEMPT-MARKERTEST-') or
            not args.run.name.endswith('-full1800') or args.output.exists()):
        raise ValueError('NOFAULT_EXPORT_SMOKE_SCOPE')
    terminal = json.loads((args.run / 'rehearsal-terminal.json').read_text())
    if (terminal['status'] != 'FULL_1800_NOFAULT_ARCHIVE_PASS' or
            terminal['faults_injected'] != 0 or
            terminal['formal_attempt_created'] is not False):
        raise ValueError('NOFAULT_SOURCE_IS_NOT_VERIFIED')
    contract = json.loads((OPS /
        'final-case-observation-v2-20261020/design/collection-contract-draft.json').read_text())
    quality = assess(collect(args.run, contract), contract)
    if quality['status'] != 'DATA_COLLECTION_ACCEPTED_FOR_ADMISSION':
        raise RuntimeError('NOFAULT_COLLECTION_GATE_BLOCKED')
    args.output.mkdir(parents=True, mode=0o700)
    write(args.output / 'quality.json', quality)
    schedule = json.loads((args.run / 'schedule.json').read_text())
    write(args.output / 'window.json', {'case_id': 'SF-017',
        'start_utc': schedule['start_utc'], 'end_utc': schedule['end_utc']})
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(OPS / 'export-runtime/cp312') + \
        (':' + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    subprocess.run(['/usr/bin/python3', '-B',
        str(OPS / 'final-case-production-v2-20261020/scripts/public_export.py'),
        '--captures', str(args.run / 'capture'),
        '--window', str(args.output / 'window.json'),
        '--quality', str(args.output / 'quality.json'),
        '--output', str(args.output / 'public'),
        '--work', str(args.output / 'private-work')], check=True,
        timeout=1800, env=environment)
    manifest = json.loads((args.output / 'public/manifest.json').read_text())
    write(args.output / 'smoke-terminal.json', {
        'status': 'NOFAULT_EXPORT_SCHEMA_PASS_NOT_OFFICIAL',
        'source_run': str(args.run), 'faults_injected': 0,
        'not_a_formal_case': True, 'row_counts': manifest['row_counts']})
    print(json.dumps({'status': 'NOFAULT_EXPORT_SCHEMA_PASS_NOT_OFFICIAL',
                      'row_counts': manifest['row_counts']}))


if __name__ == '__main__':
    main()
