#!/usr/bin/env python3
"""Run a bounded internal 30-minute Parquet conversion on archived Worker2 input."""

import json
import pathlib
import shlex
import subprocess
import tempfile

HERE = pathlib.Path(__file__).resolve().parents[1]
ROOT = pathlib.Path('/data/rca-archives/ops-competition-2026/SF30-NOFAULT-20261015')
MASTER = ('192.168.53.52', '/tmp/sfgen-master.sock')
WORKER = ('192.168.53.54', '/tmp/sfgen-w2.sock')
FLOOR = 20_000_000_000

def ssh(host, argv):
    addr, socket = host
    return ['ssh', '-S', socket, '-o', 'BatchMode=yes',
            'root@' + addr, shlex.join(list(map(str, argv)))]

def remote(host, argv, timeout=120):
    result = subprocess.run(ssh(host, argv), capture_output=True,
                            text=True, check=True, timeout=timeout)
    return result.stdout

def scp(source, host, target, reverse=False):
    addr, socket = host
    left = 'root@' + addr + ':' + str(source) if reverse else str(source)
    right = str(target) if reverse else 'root@' + addr + ':' + str(target)
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes',
                    '-o', 'ControlPath=' + socket, left, right],
                   check=True, timeout=60)

def main():
    receipt = json.loads(remote(WORKER, ['cat', ROOT/'verification.json']))
    if receipt['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS':
        raise RuntimeError('Archived raw input not independently verified')
    check = '''import pathlib,shutil,json
b=pathlib.Path(%r)
assert not (b/'preview').exists() and not (b/'preview-work').exists()
free=shutil.disk_usage('/data').free
assert free-4*1024**3>=%d
print(json.dumps({'worker2_before_export_free_bytes':free}))''' % (str(ROOT), FLOOR)
    before = json.loads(remote(WORKER, ['python3', '-c', check]))
    remote(WORKER, ['install', '-d', '-m', '700', ROOT/'preview-tools'])
    for name in ('preview_export.py', 'pilot_exchange_schema.py',
                 'verify_preview.py', 'preview_guard.py'):
        scp(HERE/'scripts'/name, WORKER, ROOT/'preview-tools'/name)
    with tempfile.TemporaryDirectory(prefix='sf30-metadata-') as cache:
        metadata = pathlib.Path(cache)/'metric-metadata.json'
        scp('/root/ops-competition-2026/sf002-delivery-20261014/results/metric-metadata.json',
            MASTER, metadata, reverse=True)
        scp(metadata, WORKER, ROOT/'metric-metadata.json')
    window_code = '''import pathlib,json
b=pathlib.Path(%r)
s=json.loads((b/'raw/schedule.json').read_text())
w={'case_id':'SF30-NOFAULT','start_utc':s['start_utc'],'end_utc':s['end_utc']}
with (b/'preview-window.json').open('x') as f:json.dump(w,f)
print(json.dumps(w))''' % str(ROOT)
    window = json.loads(remote(WORKER, ['python3', '-c', window_code]))
    result = json.loads(remote(WORKER, ['python3', '-B', ROOT/'preview-tools/preview_guard.py'], 900))
    if result['status'] != 'NOFAULT_PREVIEW_EXPORT_PASS':
        raise RuntimeError('Preview export failed')
    verify = json.loads(remote(WORKER, ['env',
        'PYTHONPATH=/data/ops-competition-2026/export-runtime/cp312',
        'python3', '-B', ROOT/'preview-tools/verify_preview.py',
        '--public', ROOT/'preview', '--output', ROOT/'preview-verification.json'], 900))
    if verify['status'] != 'NOFAULT_PREVIEW_FULL_READBACK_PASS':
        raise RuntimeError('Preview readback failed')
    output = HERE/'reports/preview-export-result.json'
    if output.exists():
        raise RuntimeError('No overwrite of preview receipt')
    output.write_text(json.dumps({'status':'NOFAULT_PARQUET_PREVIEW_PASS',
                                  'worker2_capacity':before,'window':window,
                                  'guard':result,'readback':verify},indent=2)+'\n')
    print(json.dumps({'status':'NOFAULT_PARQUET_PREVIEW_PASS',
                      'rows':verify['file_counts'],
                      'manifest_sha256':verify['preview_manifest_sha256']}))

if __name__ == '__main__':
    main()
