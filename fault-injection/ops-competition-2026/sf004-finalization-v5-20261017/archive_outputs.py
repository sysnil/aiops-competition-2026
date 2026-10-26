#!/usr/bin/env python3
"""Archive the finalized SF-004 Public/Private package to Worker3."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile


RUN_ID = 'SF-004-ATTEMPT-04'
MASTER = ('192.168.53.52', '/tmp/sfgen-master.sock')
WORKER = ('192.168.53.55', '/tmp/sfgen-w3.sock')
BASE = Path('/data/stage5-v3/ops-competition-2026')
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
ARCHIVE = Path('/data/rca-archives/ops-competition-2026') / RUN_ID
VERIFIER = Path('/root/ops-competition-2026/scripts/competition_archive.py')
LOCAL_VERIFIER = Path(__file__).resolve().parents[1] / 'scripts/competition_archive.py'
FLOOR = 5_000_000_000


def ssh(host, argv):
    address, socket = host
    return ['ssh', '-S', socket, '-o', 'BatchMode=yes', 'root@' + address,
            shlex.join(list(map(str, argv)))]


def remote(host, argv, timeout=120):
    result = subprocess.run(ssh(host, argv), capture_output=True, text=True,
                            check=True, timeout=timeout)
    return result.stdout


def copy_to_worker(source, target):
    address, socket = WORKER
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket,
                    str(source), 'root@' + address + ':' + str(target)],
                   check=True, timeout=90)


def read_json(host, path):
    return json.loads(remote(host, ['cat', path]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kind', choices=('outputs',), required=True)
    args = parser.parse_args()
    terminal = read_json(MASTER, BASE / 'runs' / RUN_ID / 'terminal.json')
    if terminal['status'] != 'SF004_FORMAL_CAPTURE_PASS_PENDING_EXPORT':
        raise RuntimeError('Complete formal capture required before any archive')
    source = BASE / 'packages' / RUN_ID
    if args.kind == 'outputs':
        public = read_json(MASTER, source / 'public/manifest.json')
        draft = read_json(MASTER, source / 'private/anomaly-gt-draft.json')
        if public['case_id'] != 'SF-004' or draft['status'] != 'DRAFT_PENDING_HUMAN_REVIEW':
            raise RuntimeError('Verified Public and private draft required')
    manifest = INDEX / (args.kind + '-source-manifest.json')
    receipt = INDEX / (args.kind + '-worker3-verification.json')
    source_readback = INDEX / (args.kind + '-master-readback.json')
    destination = ARCHIVE / args.kind
    remote(MASTER, ['install', '-d', '-m', '700', INDEX])
    if remote(MASTER, ['test', '!', '-e', manifest]) != '':
        raise RuntimeError('Source archive manifest already exists')
    source_result = json.loads(remote(MASTER,
        ['python3', '-B', VERIFIER, 'create', '--root', source,
         '--manifest', manifest], timeout=900))
    remote(MASTER, ['python3', '-B', VERIFIER, 'verify', '--root', source,
                    '--manifest', manifest, '--output', source_readback], timeout=900)
    capacity_code = '''import json,pathlib,shutil
p=pathlib.Path(%r);root=shutil.disk_usage('/').free;free=shutil.disk_usage('/data').free
assert not p.exists() and root>=%d and free-%d>=%d
p.mkdir(parents=True,mode=0o700)
print(json.dumps({'root_before':root,'data_before':free,'source_bytes':%d}))''' % (
        str(destination), FLOOR, source_result['bytes'], FLOOR,
        source_result['bytes'])
    capacity = json.loads(remote(WORKER, ['python3', '-c', capacity_code]))
    producer = subprocess.Popen(ssh(MASTER, ['tar', '-C', source, '-czf', '-', '.']),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    consumer = subprocess.Popen(ssh(WORKER, ['tar', '-C', destination, '-xzf', '-']),
                                stdin=producer.stdout, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
    producer.stdout.close()
    _, receive_error = consumer.communicate(timeout=1800)
    _, send_error = producer.communicate(timeout=30)
    if producer.returncode or consumer.returncode:
        raise RuntimeError('Archive stream failed: ' +
                           (send_error + receive_error).decode(errors='replace')[-1000:])
    with tempfile.TemporaryDirectory(prefix='sf004-archive-') as directory:
        local_manifest = Path(directory) / 'source-manifest.json'
        address, socket = MASTER
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o',
            'ControlPath=' + socket, 'root@' + address + ':' + str(manifest),
            str(local_manifest)], check=True, timeout=90)
        copy_to_worker(local_manifest, ARCHIVE / (args.kind + '-source-manifest.json'))
        copy_to_worker(LOCAL_VERIFIER, ARCHIVE / 'competition_archive.py')
    worker_receipt = ARCHIVE / (args.kind + '-verification.json')
    result = json.loads(remote(WORKER, ['python3', '-B', ARCHIVE / 'competition_archive.py',
        'verify', '--root', destination, '--manifest',
        ARCHIVE / (args.kind + '-source-manifest.json'), '--output', worker_receipt],
        timeout=900))
    if result['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS':
        raise RuntimeError('Worker3 archive verification failed')
    if args.kind == 'outputs':
        readback = '''import json,pathlib
p=pathlib.Path(%r)/'public'
names=('metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet')
for name in names:
 with (p/name).open('rb') as f:
  assert f.read(4)==b'PAR1';f.seek(-4,2);assert f.read(4)==b'PAR1'
assert json.loads((p/'manifest.json').read_text())['case_id']=='SF-004'
print('PARQUET_HEADER_FOOTER_AND_JSON_READ_PASS')''' % str(destination)
        if 'PARQUET_HEADER_FOOTER_AND_JSON_READ_PASS' not in remote(
                WORKER, ['python3', '-c', readback]):
            raise RuntimeError('Worker3 output package readback failed')
    with tempfile.TemporaryDirectory(prefix='sf004-receipt-') as directory:
        local = Path(directory) / 'verification.json'
        address, socket = WORKER
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o',
            'ControlPath=' + socket, 'root@' + address + ':' + str(worker_receipt),
            str(local)], check=True, timeout=90)
        address, socket = MASTER
        subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o',
            'ControlPath=' + socket, str(local), 'root@' + address + ':' + str(receipt)],
            check=True, timeout=90)
    print(json.dumps({'status': 'SF004_' + args.kind.upper() + '_MASTER_WORKER3_ARCHIVE_PASS',
        'file_count': source_result['file_count'], 'bytes': source_result['bytes'],
        'worker3_receipt': str(worker_receipt), 'master_index': str(receipt),
        'capacity_before': capacity}))


if __name__ == '__main__':
    main()
