"""Freeze the prospective SF-004 ATTEMPT-04 inputs after blind no-fault gates."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


OPS = Path('/root/ops-competition-2026')
ROOT = OPS / 'low-stable-v4-20261017'
DATA = Path('/data/stage5-v3/ops-competition-2026') / 'low-stable-v4-20261017'
ASSESS = DATA / 'validation-runs-v6/VAL6/assessment.json'
TRACE_HEALTH = DATA / 'validation-runs-v6/VAL6/trace-reader-health.json'
MEDIUM_ASSESS = DATA / 'validation-runs-v4/VAL4/assessment.json'
OUTPUT = ROOT / 'design/sf004-attempt04-freeze-manifest.json'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if OUTPUT.exists():
        raise RuntimeError('SF-004 ATTEMPT-04 is already frozen')
    assessment = json.loads(ASSESS.read_text())
    trace = json.loads(TRACE_HEALTH.read_text())
    medium = json.loads(MEDIUM_ASSESS.read_text())
    if (assessment['status'] != 'NO_FAULT_VALIDATION_PASS' or
            assessment['faults_injected'] != 0 or assessment['blockers'] or
            assessment['checks']['public_safe_business_identity'] is not True or
            any(assessment['missing_proxy_trace_ids'].values()) or
            trace['status'] != 'TRACE_READER_1800S_PASS' or
            trace['http_429_count'] != 0 or len(trace['chunks']) != 6 or
            medium['status'] != 'NO_FAULT_VALIDATION_PASS'):
        raise RuntimeError('Complete public-safe and medium no-fault gates required')
    files = [
        ROOT / 'scripts/freeze_sf004_attempt04.py',
        ROOT / 'design/sf004-attempt04-retry-draft.json',
        ROOT / 'design/load-profiles-v4b.yaml',
        ROOT / 'design/full-chain-low-stable-public-safe-contract.json',
        ROOT / 'formal-v2/fixed_business_streams_plain.py',
        ROOT / 'formal-v2/fixed_business_streams_faultable.py',
        *(ROOT / 'capture' / name for name in
          ('capture_live_c2.py', 'trace_storage_reader.py',
           'schedule_trace_completion.py', 'schedule_trace_completion_v2.py',
           'schedule_trace_v2.py', 'trace_identity.py', 'evaluate_trace_v2.py')),
        *(OPS / 'sf004-formal-v5-20261017' / 'design' / name for name in
          ('workload-policy.json', 'http-home-admission-draft.yaml')),
        *(OPS / directory / 'scripts' / name for directory, name in
          (('sf004-real-adapter-v5-20261017', 'prepare_sf004_formal.py'),
           ('sf004-real-adapter-v5-20261017', 'start_formal.py'),
           ('sf004-real-adapter-v5-20261017', 'sf004_workload.py'),
           ('sf004-real-adapter-v5-20261017', 'sf004_admission.py'),
           ('sf004-real-adapter-v5-20261017', 'sf004_export.py'),
           ('sf004-formal-v5-20261017', 'capture_controller.py'))),
    ]
    result = {
        'status': 'SF004_ATTEMPT04_INPUTS_FROZEN',
        'run_id': 'SF-004-ATTEMPT-04',
        'frozen_utc': datetime.now(timezone.utc).isoformat(),
        'medium_no_fault_assessment_sha256': sha(MEDIUM_ASSESS),
        'public_safe_no_fault_assessment_sha256': sha(ASSESS),
        'trace_reader_health_sha256': sha(TRACE_HEALTH),
        'source_sha256': {str(path): sha(path) for path in files},
        'faults_injected_by_qualification': 0,
        'public_gt_isolation': True,
        'minimum_filesystem_free_bytes': 5_000_000_000,
    }
    with OUTPUT.open('x') as stream:
        json.dump(result, stream, indent=2, sort_keys=True)
        stream.write('\n')
    OUTPUT.chmod(0o600)
    print(json.dumps({'status': result['status'], 'path': str(OUTPUT),
                      'sha256': sha(OUTPUT), 'files': len(files)}))


if __name__ == '__main__':
    main()
