"""Verify Master READY Public packages without contacting competition servers."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
LEDGER = OPS / 'plans/SF-003-to-SF-012-production-v2-ledger.json'
OUTPUT = OPS / 'plans/SF-003-to-SF-012-master-ready-handoff-20261018.json'
EXPECTED = {'metrics.parquet', 'logs.parquet', 'traces.parquet',
            'trace_index.parquet', 'topology.json', 'manifest.json'}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    if OUTPUT.exists():
        raise RuntimeError('Master handoff snapshot already exists')
    ledger = json.loads(LEDGER.read_text())
    ready = [case for case in ledger['official_cases']
             if case['status'] == 'READY_FOR_IMPORT']
    if len(ready) != ledger['completed_ready_for_import']:
        raise RuntimeError('READY count differs from official ledger')
    entries = []
    for case in sorted(ready, key=lambda row: row['case_id']):
        package = Path(case['package_master'])
        public = package / 'public'
        if {path.name for path in public.iterdir()} != EXPECTED:
            raise RuntimeError(case['case_id'] + ': Public file whitelist mismatch')
        manifest_path = public / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        if (sha(manifest_path) != case['public_manifest_sha256'] or
                manifest['case_id'] != case['case_id'] or
                manifest['private_leak_scan'] != 'PASS'):
            raise RuntimeError(case['case_id'] + ': Manifest identity or leak audit mismatch')
        window = manifest['window']
        duration = (datetime.fromisoformat(window['end_utc']) -
                    datetime.fromisoformat(window['start_utc'])).total_seconds()
        if duration != 1800:
            raise RuntimeError(case['case_id'] + ': Not a complete 30-minute window')
        for name, metadata in manifest['files'].items():
            if sha(public / name) != metadata['sha256']:
                raise RuntimeError(case['case_id'] + ': File SHA mismatch: ' + name)
        gt = json.loads((package / 'private/anomaly-gt-draft.json').read_text())
        if gt['status'] != 'DRAFT_PENDING_HUMAN_REVIEW':
            raise RuntimeError(case['case_id'] + ': Private GT status changed')
        entries.append({'case_id': case['case_id'], 'attempt_id': case['run_id'],
                        'public_path_on_master': str(public),
                        'public_manifest_sha256': sha(manifest_path),
                        'public_file_count': len(EXPECTED),
                        'public_total_bytes': sum((public / name).stat().st_size
                                                  for name in EXPECTED),
                        'window': window, 'row_counts': manifest['row_counts'],
                        'trace_index_rows': manifest['trace_index_rows'],
                        'gt_review_state': gt['status'],
                        'ledger_server_import_marker': case.get('server_import'),
                        'competition_server_51_state': 'NOT_CHECKED_THIS_TURN'})
    result = {'status': 'MASTER_READY_PUBLIC_PACKAGES_VERIFIED',
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'ledger_sha256': sha(LEDGER), 'ready_cases': len(entries),
              'remaining_cases': ledger['remaining'],
              'competition_server_contacted': False,
              'entries': entries}
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'ready_cases': len(entries),
                      'remaining_cases': result['remaining_cases'],
                      'sha256': sha(OUTPUT)}))


if __name__ == '__main__':
    main()
