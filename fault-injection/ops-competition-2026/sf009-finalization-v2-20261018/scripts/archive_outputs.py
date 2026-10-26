#!/usr/bin/env python3
"""Archive a verified SF-009 output package on Worker2."""

import json
from pathlib import Path
import shlex
import subprocess
import tempfile


RUN_ID = 'SF-009-ATTEMPT-04'
SOURCE = Path('/data/stage5-v3/ops-competition-2026/packages') / RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index') / RUN_ID
DEST = Path('/data/rca-archives/ops-competition-2026') / RUN_ID
ARCHIVER = Path('/root/ops-competition-2026/scripts/competition_archive.py')
LOCAL_ARCHIVER = Path(__file__).resolve().parents[2] / 'scripts/competition_archive.py'
HOSTS = {'master': ('192.168.53.52', '/tmp/sfgen-master.sock'),
         'w2': ('192.168.53.54', '/tmp/sfgen-w2.sock')}
FLOOR = 5_000_000_000


def ssh(host, argv):
    address, socket = HOSTS[host]
    return ['ssh', '-S', socket, '-o', 'BatchMode=yes',
            'root@' + address, shlex.join([str(item) for item in argv])]


def remote(host, argv, timeout=900):
    result = subprocess.run(ssh(host, argv), capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(host + ': ' + result.stderr[-1000:])
    return result.stdout


def scp(source, host, target, reverse=False):
    address, socket = HOSTS[host]
    left = f'root@{address}:{source}' if reverse else str(source)
    right = str(target) if reverse else f'root@{address}:{target}'
    subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket,
                    left, right], check=True, timeout=60)


if __name__ == '__main__':
    verify = '''import hashlib,json,pathlib
from datetime import datetime
p=pathlib.Path(%r);m=json.loads((p/'public/manifest.json').read_text())
assert m['case_id']=='SF-009' and m['window']['run_id']=='SF-009-ATTEMPT-04'
assert (datetime.fromisoformat(m['window']['end_utc'])-datetime.fromisoformat(m['window']['start_utc'])).total_seconds()==1800
assert m['private_leak_scan']=='PASS' and all(m['row_counts'][k]>0 for k in ('metrics','logs','traces'))
assert {f.name for f in (p/'public').iterdir()}=={'metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet','topology.json','manifest.json'}
for name,meta in m['files'].items():
 assert hashlib.sha256((p/'public'/name).read_bytes()).hexdigest()==meta['sha256']
assert json.loads((p/'private/anomaly-gt-draft.json').read_text())['status']=='DRAFT_PENDING_HUMAN_REVIEW'
print(json.dumps({'public_files':6,'status':'PUBLIC_SHA_AND_PRIVATE_DRAFT_BOUNDARY_PASS'}))''' % str(SOURCE)
    check = json.loads(remote('master', ['python3', '-c', verify]))
    if check['status'] != 'PUBLIC_SHA_AND_PRIVATE_DRAFT_BOUNDARY_PASS':
        raise RuntimeError('SF-009 package not ready for archive')
    source_manifest = INDEX / 'output-manifest.json'
    source = json.loads(remote('master', ['python3', '-B', ARCHIVER, 'create',
        '--root', SOURCE, '--manifest', source_manifest]))
    if not source.get('file_count') or not source.get('bytes'):
        raise RuntimeError('Output package has no immutable source files')
    receipts = {}
    with tempfile.TemporaryDirectory(prefix='sf009-output-archive-') as directory:
        local = Path(directory) / 'output-manifest.json'
        scp(source_manifest, 'master', local, reverse=True)
        for host in ('w2',):
            target = DEST / 'outputs'
            check = '''import json,pathlib,shutil
p=pathlib.Path(%r);root=shutil.disk_usage('/').free;data=shutil.disk_usage('/data').free
assert not p.exists() and root >= %d and data - %d >= %d
p.mkdir(parents=True,mode=0o700)
print(json.dumps({'root_free':root,'data_free':data}))''' % (
                str(target), FLOOR, source['bytes'], FLOOR)
            capacity = json.loads(remote(host, ['python3', '-c', check]))
            reader = subprocess.Popen(ssh('master', ['tar', '-C', SOURCE, '-czf', '-', '.']),
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            writer = subprocess.Popen(ssh(host, ['tar', '-C', target, '-xzf', '-']),
                                      stdin=reader.stdout, stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE)
            reader.stdout.close()
            _, writer_error = writer.communicate(timeout=900)
            _, reader_error = reader.communicate(timeout=30)
            if writer.returncode or reader.returncode:
                raise RuntimeError(host + ' copy: ' +
                    (reader_error + writer_error).decode(errors='replace')[-1000:])
            scp(local, host, DEST / 'output-manifest.json')
            scp(LOCAL_ARCHIVER, host, DEST / 'competition_archive.py')
            receipt = DEST / 'output-verification.json'
            verification = json.loads(remote(host, ['python3', '-B',
                DEST / 'competition_archive.py', 'verify', '--root', target,
                '--manifest', DEST / 'output-manifest.json', '--output', receipt]))
            if verification['status'] != 'COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS':
                raise RuntimeError(host + ' output verification failed')
            parquet_read = '''import pathlib,json
p=pathlib.Path(%r);out={}
for name in ('metrics.parquet','logs.parquet','traces.parquet','trace_index.parquet'):
 f=p/name
 with f.open('rb') as stream:
  header=stream.read(4);stream.seek(-4,2);footer=stream.read(4)
 assert header==footer==b'PAR1'
 out[name]={'bytes':f.stat().st_size,'header_footer':'PAR1'}
print(json.dumps(out))''' % str(target / 'public')
            parquet_magic = json.loads(remote(host, ['python3', '-c', parquet_read]))
            local_receipt = Path(directory) / (host + '-output-verification.json')
            scp(receipt, host, local_receipt, reverse=True)
            scp(local_receipt, 'master', INDEX / local_receipt.name)
            receipts[host] = {'capacity_before': capacity,
                              'verification': verification,
                              'parquet_readback': parquet_magic}
    print(json.dumps({'status': 'SF009_OUTPUT_MASTER_WORKER2_ARCHIVE_PASS',
                      'file_count': source['file_count'], 'bytes': source['bytes'],
                      'workers': sorted(receipts)}))
