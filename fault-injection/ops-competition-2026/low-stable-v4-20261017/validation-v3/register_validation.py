"""Write the immutable candidate contract before any 1800-second no-fault run."""

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path('/root/ops-competition-2026/low-stable-v4-20261017')
SCRIPTS = ROOT / 'validation-v3'
SOURCES = [
    *(SCRIPTS / name for name in ('common.py', 'observation.py', 'run_validation.py',
                                  'evaluate.py', 'fixed_business_streams_v4.py')),
    *(ROOT / 'capture' / name for name in ('capture_live_c2.py', 'trace_storage_reader.py')),
    Path('/root/stage5-v3-autonomous-preparation/design/load-profiles-c3.yaml'),
    Path('/root/ops-competition-2026/scripts/competition_controls.py'),
    Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py'),
    Path('/root/stage5-v3-observability-diagnostics/snapshots/reference/workload-state-machine-c3.yaml'),
    Path('/root/cross-layer-workload-generation/design/people.json'),
    Path('/root/stage5-v3-clean-gate-v2/scripts/schedule_trace_completion_v2.py'),
    Path('/root/stage5-v3-clean-gate-v2/scripts/schedule_trace_v2.py'),
]


def main():
    collector = json.loads(subprocess.check_output([
        'kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '-n', 'otel-demo',
        'get', 'configmap', 'otel-collector', '-o', 'json'], text=True))
    collector_sha = hashlib.sha256(collector['data']['relay'].encode()).hexdigest()
    plan = {
        'status': 'PREREGISTERED_MEDIUM_TRACE_READER_REPAIR_1800S',
        'created_utc': datetime.now(timezone.utc).isoformat(),
        'collector_config_sha256': collector_sha,
        'faults_allowed': False,
        'runs': {'VAL3': {'seed': 751703, 'duration_seconds': 1800}},
        'workload': {
            'profile': 'MEDIUM', 'policy_name': 'TRACE_REPAIR_MEDIUM',
            'arrival_rate_sessions_per_second': 1.4,
            'control_streams': [
                {'name': 'image_asset', 'rate_per_second': 1.0, 'seed_offset': 101},
                {'name': 'cart_view', 'rate_per_second': 2.0, 'seed_offset': 103},
            ],
            'control_timeout_seconds': 8,
            'control_concurrency_per_stream': {
                'image_asset': 3, 'cart_view': 6},
        },
        'criteria': {
            'duration_seconds': 1800, 'chunk_seconds': 300,
            'trace_audit_offsets_seconds': [5, 60, 120, 240],
            'all_three_modalities_each_minute': True,
            'all_receiver_refused_signal_deltas': 0,
            'all_expected_sampled_request_proxy_match_fraction': 1.0,
            'unexplained_missing_request_ids': 0,
            'control_successes_per_minute_min': 30,
            'target_successes_per_minute_min': 30,
            'control_p95_ratio_max': 1.25,
            'collector_restarts_or_oom': 0,
            'collector_working_set_stop_bytes': 380 * 1024**2,
            'log_trace_storage_age_seconds_max': 60,
            'exporter_queue_peak_capacity_fraction_max': 0.01,
            'exporter_queue_final_growth_bytes_max': 1_048_576,
            'opensearch_parent_breaker_new_trips': 0,
            'no_business_fault': True,
        },
        'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCES},
    }
    target = ROOT / 'design/medium-trace-repair-contract.json'
    with target.open('x') as stream:
        json.dump(plan, stream, indent=2, sort_keys=True)
        stream.write('\n')
    target.chmod(0o600)
    print(json.dumps({'status': plan['status'], 'path': str(target),
                      'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))


if __name__ == '__main__':
    main()
