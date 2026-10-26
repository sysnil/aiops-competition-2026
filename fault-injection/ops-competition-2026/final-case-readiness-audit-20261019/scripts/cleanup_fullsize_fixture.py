#!/usr/bin/env python3
"""Remove only verified synthetic payload bytes, retaining manifests/receipts."""

import argparse
import json
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from fullsize_archive import DEST, FILES, HOSTS, SOURCE, sha


def remote(host, code):
    address, control = HOSTS[host]
    response = subprocess.run(
        ['ssh', '-S', control, '-o', 'BatchMode=yes', 'root@' + address,
         shlex.join(['python3', '-c', code])],
        capture_output=True, text=True, timeout=60)
    if response.returncode:
        raise RuntimeError(host + ': ' + response.stderr[-800:])
    return json.loads(response.stdout.splitlines()[-1])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Immutable cleanup output exists')
    qualification = json.loads(args.qualification.read_text())
    if (qualification['status'] !=
            'THREE_WORKER_FULLSIZE_ARCHIVE_AND_READBACK_PASS' or
            len(qualification['worker_results']) != 3 or
            {row['host'] for row in qualification['worker_results']} != set(HOSTS)):
        raise RuntimeError('Full-size qualification not complete')
    source_manifest = SOURCE / 'source-manifest.json'
    manifest_sha = qualification['fixture']['source_manifest_sha256']
    data = json.loads(source_manifest.read_text())
    if (sha(source_manifest) != manifest_sha or
            data['status'] != 'SYNTHETIC_NO_SENSITIVE_FULLSIZE_ARCHIVE' or
            set(data['files']) != set(FILES) or
            any(data['files'][key]['bytes'] != size
                for key, size in FILES.items())):
        raise RuntimeError('Master synthetic fixture identity changed')
    result = {'status': 'SYNTHETIC_CLEANUP_INCOMPLETE',
              'qualification_sha256': sha(args.qualification),
              'source_manifest_sha256': manifest_sha,
              'historical_archives_deleted': False, 'worker_results': []}
    worker_code = '''import hashlib,json,os
from pathlib import Path
root=Path(%r)
manifest=root/'archive/source-manifest.json'
receipt=root/'verification.json'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(4*1024*1024),b''):h.update(block)
    return h.hexdigest()
if sha(manifest)!=%r or sha(receipt)!=%r:raise RuntimeError('Synthetic receipt mismatch')
data=json.loads(manifest.read_text());proof=json.loads(receipt.read_text())
if data['status']!='SYNTHETIC_NO_SENSITIVE_FULLSIZE_ARCHIVE' or proof['status']!='FULLSIZE_ARCHIVE_SHA_AND_RESTORED_READ_PASS':raise RuntimeError('Not a verified synthetic archive')
removed=[]
for base in (root/'archive',root/'restored'):
    for rel,size in %r.items():
        path=base/rel
        if path.is_symlink() or not path.is_file() or path.stat().st_size!=size:raise RuntimeError('Fixture file mismatch: '+str(path))
        path.unlink();removed.append(str(path))
print(json.dumps({'status':'VERIFIED_SYNTHETIC_BYTES_REMOVED','removed':removed,'root_free_bytes':os.statvfs('/').f_bavail*os.statvfs('/').f_frsize}))''' 
    try:
        for row in qualification['worker_results']:
            host = row['host']
            code = worker_code % (str(DEST), manifest_sha,
                                  row['worker_receipt_sha256'], FILES)
            result['worker_results'].append({'host': host, **remote(host, code)})
        removed = []
        for rel, size in FILES.items():
            path = SOURCE / rel
            if (path.is_symlink() or not path.is_file() or
                    path.stat().st_size != size):
                raise RuntimeError('Master synthetic file changed: ' + rel)
            path.unlink()
            removed.append(str(path))
        result['master_removed'] = removed
        result['master_data_free_bytes'] = shutil.disk_usage('/data/stage5-v3').free
        result['status'] = 'VERIFIED_SYNTHETIC_PAYLOAD_CLEANED'
    except BaseException as exc:
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    finally:
        result['at_utc'] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'status': result['status'],
                          'workers_cleaned': len(result['worker_results']),
                          'error': result.get('error')}), flush=True)
    if result['status'] != 'VERIFIED_SYNTHETIC_PAYLOAD_CLEANED':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
