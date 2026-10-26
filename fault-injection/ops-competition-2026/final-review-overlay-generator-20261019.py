"""Build a minimal private review overlay from immutable Master evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


CASES = {
    'SF-004': '05', 'SF-006': '01', 'SF-007': '08', 'SF-008': '09',
    'SF-009': '04', 'SF-011': '05', 'SF-012': '04',
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp has no timezone')
    return parsed.astimezone(timezone.utc)


def summarize(plan: dict, record: dict, result: dict | None) -> list[dict]:
    if record.get('http_request_delay'):
        detail = record['http_request_delay']
        return [{
            'mechanism': 'HTTP request delay',
            'root_layer': 'WORKLOAD_REQUEST_PATH',
            'target': 'request path; specific server entity not established by this record',
            'start_utc': utc(detail['planned_start_utc']).isoformat(),
            'end_utc': utc(detail['planned_end_utc']).isoformat(),
            'time_basis': 'planned bounded interval, corroborated by observed delayed requests',
            'first_observed_utc': utc(detail['first_observed_utc']).isoformat(),
            'last_observed_utc': utc(detail['last_observed_utc']).isoformat(),
            'observed_request_count': int(detail['observed_delayed_request_count']),
        }]
    actions = plan.get('actions') or []
    if not isinstance(actions, list) or len(actions) != 1:
        raise ValueError('expected exactly one bounded fault action')
    action = actions[0]
    if not isinstance(action, dict):
        raise ValueError('invalid action')
    if result:
        start, end = result['actual_start_utc'], result['actual_end_utc']
    else:
        faults = record.get('faults')
        if not isinstance(faults, dict) or len(faults) != 1:
            raise ValueError('actual fault timing is unavailable')
        fault = next(iter(faults.values()))
        start, end = fault['actual_start_utc'], fault['actual_end_utc']
    if utc(start) >= utc(end):
        raise ValueError('fault interval is invalid')
    targets = action.get('targets') or []
    if not targets:
        raise ValueError('fault target is unavailable')
    return [{
        'mechanism': str(action['fault_type']),
        'root_layer': str(action['mechanism_id']),
        'target': ', '.join(sorted({str(item.get('service') or item.get('name')) for item in targets})),
        'target_pods': [
            {'service': str(item.get('service') or ''), 'pod': str(item.get('name') or ''),
             'uid': str(item.get('uid') or ''), 'node': str(item.get('node') or '')}
            for item in targets
        ],
        'start_utc': utc(start).isoformat(),
        'end_utc': utc(end).isoformat(),
        'time_basis': 'private bounded injection record, not observable GT',
    }]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--packages', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('review overlay destination already exists')
    args.output.mkdir(parents=True, mode=0o700)
    os.chmod(args.output, 0o700)
    files = {}
    for case_id, attempt in CASES.items():
        package = args.packages / f'{case_id}-ATTEMPT-{attempt}'
        private = package / 'private'
        public = package / 'public'
        paths = {key: private / name for key, name in {
            'plan': 'injection-plan.json', 'record': 'injection-record.json',
            'draft': 'anomaly-gt-draft.json', 'result': 'injection-result.json',
        }.items()}
        public_manifest = public / 'manifest.json'
        if any(path.is_symlink() for path in [*paths.values(), public_manifest]):
            raise ValueError(f'{case_id}: symlink source refused')
        draft = json.loads(paths['draft'].read_text())
        if draft.get('status') != 'DRAFT_PENDING_HUMAN_REVIEW':
            raise ValueError(f'{case_id}: GT review status changed')
        if draft.get('source_public_manifest_sha256') != digest(public_manifest):
            raise ValueError(f'{case_id}: draft/Public source mismatch')
        manifest = json.loads(public_manifest.read_text())
        left, right = utc(manifest['window']['start_utc']), utc(manifest['window']['end_utc'])
        result = json.loads(paths['result'].read_text()) if paths['result'].is_file() else None
        events = summarize(json.loads(paths['plan'].read_text()),
                           json.loads(paths['record'].read_text()), result)
        if any(not left <= utc(row['start_utc']) < utc(row['end_utc']) <= right for row in events):
            raise ValueError(f'{case_id}: injection outside Public case window')
        payload = {
            'case_id': case_id, 'attempt_id': f'{case_id}-ATTEMPT-{attempt}',
            'classification': 'PRIVATE_REVIEW_ONLY',
            'draft_status': draft['status'], 'draft_event_count': len(draft['events']),
            'public_manifest_sha256': digest(public_manifest),
            'source_sha256': {key: digest(path) for key, path in paths.items() if path.is_file()},
            'injections': events,
        }
        output = args.output / f'{case_id}.json'
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        os.chmod(output, 0o600)
        files[output.name] = digest(output)
    index = {'classification': 'PRIVATE_REVIEW_ONLY', 'files': files,
             'case_ids': sorted(CASES), 'generated_from': 'Master immutable private evidence'}
    path = args.output / 'manifest.json'
    path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    os.chmod(path, 0o600)
    print(json.dumps({'status': 'PRIVATE_REVIEW_OVERLAY_PREPARED', 'cases': len(files),
                      'manifest_sha256': digest(path)}))


if __name__ == '__main__':
    main()
