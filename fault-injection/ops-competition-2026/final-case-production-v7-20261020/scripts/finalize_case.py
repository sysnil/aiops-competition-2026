#!/usr/bin/env python3
"""Finalize one completed 30-minute Case without publishing or approving GT."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'final-case-production-v7-20261020'
FORMAL = OPS / 'final-case-formal-entry-v7-20261020/scripts'
DATA = Path('/data/stage5-v3/ops-competition-2026')
sys.path.insert(0, str(FORMAL))
from check_gt_event_budget import assess as assess_gt_budget


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write('\n')
    path.chmod(0o600)


def command(argv, *, timeout=1800):
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(OPS / 'export-runtime/cp312') + \
        (':' + environment['PYTHONPATH'] if environment.get('PYTHONPATH') else '')
    result = subprocess.run(argv, capture_output=True, text=True,
                            timeout=timeout, env=environment)
    if result.returncode:
        raise RuntimeError('FINALIZE_SUBPROCESS_FAILED:' +
                           Path(argv[2]).name + ':' + result.stderr[-700:])
    return result.stdout


def finalize(run_id):
    case = run_id.split('-ATTEMPT-', 1)[0]
    if case not in {f'SF-{i:03d}' for i in range(13, 18)}:
        raise ValueError('FINALIZE_CASE_SCOPE')
    run = DATA / 'runs' / run_id
    if read(run / 'capture-terminal.json')['status'] != \
            'FORMAL_CAPTURE_COMPLETE_PENDING_ADMISSION':
        raise RuntimeError('FINALIZE_CAPTURE_NOT_COMPLETE')
    export = DATA / 'exports' / run_id
    scripts = ROOT / 'scripts'
    if not (export / 'private-export-verified.json').is_file():
        command(['/usr/bin/python3', '-B', str(scripts / 'prepare_export.py'),
                 '--run-id', run_id])
    public = export / 'public'
    if not (run / 'admission.json').is_file():
        try:
            command(['/usr/bin/python3', '-B', str(scripts / 'admission.py'),
                     '--run-id', run_id])
        except RuntimeError:
            if not (run / 'admission.json').is_file():
                raise
    admission = read(run / 'admission.json')
    if admission['status'] != 'ADMITTED_FOR_GT_DRAFT':
        return {'status': 'CASE_INVALID_RETAIN_RAW_AND_PUBLIC',
                'run_id': run_id, 'admission_blockers': admission['blockers']}
    private = export / 'private'
    if not (private / 'anomaly-gt-draft.json').is_file():
        command(['/usr/bin/python3', '-B', str(scripts / 'draft_anomalies.py'),
                 '--public', str(public), '--output', str(private),
                 '--reference', str(ROOT / 'design/anomaly-reference-draft.json')])
    if not (private / 'injection-plan.json').is_file():
        freeze = read(run / 'control/freeze-reference.json')
        write(private / 'injection-plan.json', read(Path(freeze['manifest_path'])))
    if not (private / 'injection-record.json').is_file():
        write(private / 'injection-record.json',
              read(run / 'control/action-results.json'))
    if not (private / 'gt-budget.json').is_file():
        budget = assess_gt_budget(read(private / 'anomaly-candidates.json'),
                                  read(private / 'anomaly-gt-draft.json'))
        write(private / 'gt-budget.json', budget)
    budget = read(private / 'gt-budget.json')
    if not (run / 'archive-result.json').is_file():
        command(['/usr/bin/python3', '-B', str(scripts / 'archive_case.py'),
                 '--run-id', run_id], timeout=3600)
    archived = read(run / 'archive-result.json')
    status = ('READY_FOR_PROTECTED_PUBLIC_STAGING'
              if budget['status'] == 'GT_SHORTLIST_READY_FOR_HUMAN_REVIEW' and
                 archived['status'] == 'TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS'
              else 'PUBLIC_BUNDLE_ARCHIVED_GT_SHORTLIST_REVIEW_PENDING')
    return {'status': status, 'case_id': case, 'run_id': run_id,
            'public_path': str(public),
            'public_manifest_sha256': read(export / 'private-export-verified.json')[
                'manifest_sha256'],
            'admission_status': admission['status'],
            'gt_budget_status': budget['status'],
            'proposed_scoring_events': budget['proposed_scoring_event_count'],
            'gt_review_status': 'DRAFT_PENDING_HUMAN_REVIEW',
            'gt_scoring_approved': False,
            'public_staging_allowed': archived['status'] ==
                'TWO_HOST_ARCHIVE_SHA_AND_READBACK_PASS',
            'contest_api_published': False,
            'archive_status': archived['status'],
            'finalized_utc': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    result = finalize(args.run_id)
    path = DATA / 'runs' / args.run_id / 'finalization.json'
    if path.exists():
        raise ValueError('FINALIZATION_IMMUTABLE')
    write(path, result)
    print(json.dumps(result, sort_keys=True))
    if result['status'] not in ('READY_FOR_PROTECTED_PUBLIC_STAGING',
                                'PUBLIC_BUNDLE_ARCHIVED_GT_SHORTLIST_REVIEW_PENDING'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
