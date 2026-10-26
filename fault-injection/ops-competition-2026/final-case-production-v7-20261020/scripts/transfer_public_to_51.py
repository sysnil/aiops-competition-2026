#!/usr/bin/env python3
"""Transfer verified final Public files to protected 51 staging only."""

import argparse
import hashlib
import json
import shlex
import subprocess
import time
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
HOST = '192.168.51.51'
SOCKET = '/run/ops-final-ssh/c51.sock'
STAGING = Path('/data/aiops-contest/staging')
PUBLIC_NAMES = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
                'trace_index.parquet', 'topology.json', 'manifest.json'}
FLOOR = 5_000_000_000


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            digest.update(block)
    return digest.hexdigest()


def remote(argv):
    result = subprocess.run(['ssh', '-S', SOCKET, '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=3', 'root@' + HOST, shlex.join(map(str, argv))],
        capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('TRANSFER_51_COMMAND_FAILED:' + result.stderr[-350:])
    return result.stdout


def remote_free():
    result = remote(['df', '-B1', '--output=avail', STAGING]).splitlines()
    if len(result) != 2:
        raise RuntimeError('TRANSFER_51_DF_PARSE')
    return int(result[1])


def transfer(run_id):
    case = run_id.split('-ATTEMPT-', 1)[0]
    if case not in {f'SF-{number:03d}' for number in range(13, 18)}:
        raise ValueError('TRANSFER_CASE_SCOPE')
    raw = DATA / 'runs' / run_id
    export = DATA / 'exports' / run_id
    public = export / 'public'
    if (not public.is_dir() or public.is_symlink() or
            {path.name for path in public.iterdir()} != PUBLIC_NAMES or
            json.loads((raw / 'admission.json').read_text())['status'] !=
                'ADMITTED_FOR_GT_DRAFT' or
            json.loads((raw / 'archive-result.json').read_text())['status'] !=
                'TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS'):
        raise RuntimeError('TRANSFER_SOURCE_NOT_READY_OR_ARCHIVED')
    manifest = json.loads((public / 'manifest.json').read_text())
    if set(manifest['files']) != PUBLIC_NAMES - {'manifest.json'}:
        raise RuntimeError('TRANSFER_PUBLIC_MANIFEST_WHITELIST')
    for name, item in manifest['files'].items():
        path = public / name
        if path.is_symlink() or sha(path) != item['sha256'] or \
                path.stat().st_size != item['bytes']:
            raise RuntimeError('TRANSFER_SOURCE_HASH_OR_SIZE:' + name)
    before = remote_free()
    bytes_total = sum(path.stat().st_size for path in public.iterdir())
    if before - bytes_total < FLOOR:
        raise RuntimeError('TRANSFER_51_FIVE_GB_FLOOR')
    final = STAGING / (case + '-incoming')
    temporary = STAGING / ('.' + case + '-' + run_id + '.incoming')
    remote(['test', '!', '-e', final])
    remote(['test', '!', '-e', temporary])
    remote(['mkdir', '-p', '-m', '700', temporary])
    started = time.time()
    subprocess.run(['rsync', '-a', '--checksum', '--partial', '-e',
        'ssh -S ' + SOCKET + ' -o BatchMode=yes',
        *[str(public / name) for name in sorted(PUBLIC_NAMES)],
        'root@' + HOST + ':' + str(temporary) + '/'],
        check=True, capture_output=True, timeout=900)
    remote(['chmod', '700', temporary])
    remote(['chmod', '600', *[temporary / name for name in PUBLIC_NAMES]])
    script = ('import hashlib,json,pathlib,sys; p=pathlib.Path(sys.argv[1]); '
        'm=json.loads((p/"manifest.json").read_text()); '
        'names={x.name for x in p.iterdir()}; '
        'assert names=={"metrics.parquet","logs.parquet","traces.parquet",'
        '"trace_index.parquet","topology.json","manifest.json"}; '
        'assert all(hashlib.sha256((p/n).read_bytes()).hexdigest()==v["sha256"] '
        'and (p/n).stat().st_size==v["bytes"] for n,v in m["files"].items()); '
        'assert all((p/n).open("rb").read(4)==b"PAR1" and '
        '(p/n).open("rb").read()[-4:]==b"PAR1" for n in '
        '("metrics.parquet","logs.parquet","traces.parquet","trace_index.parquet")); '
        'print(json.dumps({"manifest_sha256":hashlib.sha256((p/"manifest.json").read_bytes()).hexdigest(),'
        '"files":len(names),"bytes":sum((p/n).stat().st_size for n in names)}))')
    verified = json.loads(remote(['python3', '-c', script, temporary]))
    if (verified['manifest_sha256'] != sha(public / 'manifest.json') or
            verified['files'] != 6 or verified['bytes'] != bytes_total or
            remote_free() < FLOOR):
        raise RuntimeError('TRANSFER_TARGET_VERIFICATION_FAILED')
    remote(['mv', '-T', temporary, final])
    return {'status': 'PUBLIC_TRANSFER_SHA_VERIFIED_NOT_PUBLISHED',
            'run_id': run_id, 'case_id': case,
            'source': str(public), 'target_host': HOST,
            'target': str(final), 'files': verified['files'],
            'bytes': bytes_total, 'manifest_sha256': verified['manifest_sha256'],
            'target_free_before_bytes': before,
            'target_free_after_bytes': remote_free(),
            'elapsed_seconds': time.time() - started,
            'api_published': False, 'private_transferred': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = transfer(args.run_id)
    path = OPS / 'reports' / (args.run_id + '-transfer-to-51.json')
    with path.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
