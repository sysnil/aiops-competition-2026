#!/usr/bin/env python3
"""Retain failed preview v1; correct only the window binding in a new v2 run."""

import json
import pathlib
import shlex
import subprocess

HERE = pathlib.Path(__file__).resolve().parents[1]
BASE = pathlib.Path('/data/rca-archives/ops-competition-2026/SF30-NOFAULT-20261015')
ADDR = 'root@192.168.53.54'
SOCKET = '/tmp/sfgen-w2.sock'

def remote(args, timeout=900):
    result = subprocess.run(['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
                             ADDR, shlex.join(list(map(str, args)))],
                            capture_output=True, text=True, check=True,
                            timeout=timeout)
    return json.loads(result.stdout)

def main():
    prior = remote(['python3', '-c',
        'import json,pathlib;print(json.dumps(json.loads(pathlib.Path('+
        repr(str(BASE/'preview-guard-result.json'))+').read_text())))'])
    if prior['status'] != 'NOFAULT_PREVIEW_EXPORT_FAILED' or 'EXPORT_EXIT_1' != prior.get('error'):
        raise RuntimeError('Failed first preview record is not intact')
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
                    '-o', 'ControlPath=' + SOCKET,
                    str(HERE/'scripts/preview_guard_v2.py'),
                    ADDR + ':' + str(BASE/'preview-tools/preview_guard_v2.py')],
                   check=True, timeout=60)
    guard = remote(['python3', '-B', BASE/'preview-tools/preview_guard_v2.py'])
    if guard['status'] != 'NOFAULT_PREVIEW_EXPORT_PASS':
        raise RuntimeError('Preview v2 conversion failed')
    readback = remote(['env', 'PYTHONPATH=/data/ops-competition-2026/export-runtime/cp312',
        'python3', '-B', BASE/'preview-tools/verify_preview.py',
        '--public', BASE/'preview-v2', '--output', BASE/'preview-v2-verification.json'])
    if readback['status'] != 'NOFAULT_PREVIEW_FULL_READBACK_PASS':
        raise RuntimeError('Preview v2 readback failed')
    result = {'status':'NOFAULT_PARQUET_PREVIEW_V2_PASS',
              'v1_failure_preserved': True, 'guard':guard, 'readback':readback}
    output = HERE/'reports/preview-export-v2-result.json'
    if output.exists():
        raise RuntimeError('No overwrite')
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'rows':readback['file_counts'],
                      'manifest_sha256':readback['preview_manifest_sha256']}))

if __name__ == '__main__':
    main()
