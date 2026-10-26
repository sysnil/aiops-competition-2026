#!/usr/bin/env python3
"""Pre-register two fresh 30-minute no-fault Collector validations."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/root/ops-competition-2026/collector-debug-repair-20261016')
SCRIPTS = ROOT / 'scripts-v3'
REFERENCES = [
    SCRIPTS / name for name in ('change.py', 'common.py', 'observation.py',
                                'run_validation.py', 'evaluate.py')
] + [
    Path(p) for p in (
        '/root/ops-competition-2026/scripts/competition_controls.py',
        '/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py',
        '/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml',
        '/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml',
        '/root/cross-layer-workload-generation/design/people.json',
        '/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py',
        '/root/stage5-v3-autonomous-preparation/scripts/capture_live_c2.py',
        '/root/stage5-v3-clean-gate-v2/scripts/schedule_trace_completion_v2.py',
        '/root/stage5-v3-clean-gate-v2/scripts/schedule_trace_v2.py',
    )
]

if __name__ == '__main__':
    plan = {
        'status': 'PREREGISTERED_TWO_FRESH_1800S_NOFAULT_WINDOWS',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'collector_candidate_sha256': json.loads((ROOT / 'plan.json').read_text())['candidate_sha256'],
        'faults_allowed': False,
        'runs': {
            'VAL1': {'seed': 741601, 'duration_seconds': 1800},
            'VAL2': {'seed': 741602, 'duration_seconds': 1800},
        },
        'workload': {
            'profile': 'MEDIUM', 'arrival_rate_sessions_per_second': 1.4,
            'control_streams': [
                {'name': 'image_asset', 'rate_per_second': 1.0, 'seed_offset': 101},
                {'name': 'cart_view', 'rate_per_second': 2.0, 'seed_offset': 103},
            ],
            'control_timeout_seconds': 8,
            'control_concurrency_per_stream': {'image_asset': 3, 'cart_view': 6},
        },
        'control_concurrency_note': 'Prospective capacity change from 2/4 after SF-005 ATTEMPT-02; request rates and timeout unchanged',
        'criteria': {
            'duration_seconds': 1800, 'chunk_seconds': 300,
            'trace_audit_offsets_seconds': [5, 60, 120, 240],
            'all_three_modalities_each_minute': True,
            'all_receiver_refused_signal_deltas': 0,
            'all_expected_sampled_request_proxy_match_fraction': 1.0,
            'image_request_count': 1800, 'cart_request_count': 3600,
            'unexplained_missing_request_ids': 0,
            'collector_restarts_or_oom': 0,
            'collector_working_set_stop_bytes': 380 * 1024**2,
            'log_trace_storage_age_seconds_max': 60,
            'exporter_queue_peak_capacity_fraction_max': 0.01,
            'exporter_queue_final_growth_bytes_max': 1_048_576,
            'queue_units': 'configured exporter sizer; OpenSearch/Prometheus/Jaeger use bytes',
            'no_business_fault': True,
        },
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in REFERENCES},
    }
    target = ROOT / 'plan-validation-v3.json'
    with target.open('x') as stream:
        json.dump(plan, stream, indent=2, sort_keys=True)
        stream.write('\n')
    target.chmod(0o600)
    print(json.dumps({'status': plan['status'], 'path': str(target),
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
