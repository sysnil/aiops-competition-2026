#!/usr/bin/env python3
"""Immutable, source-linked conclusion for the single Collector candidate window."""

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import yaml

from clean_protocol import save_new, sha
from run_collector_study import summarize_monitor


ROOT = Path('/root/stage5-v3-final-fast-ready')
RUN = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-COLLECTOR-20261013')
WATCH = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-RECOVERY-WATCH-20261013')


def read(path): return json.loads(path.read_text())


def main():
    sources = [RUN / 'schedule.json', RUN / 'result.json',
        RUN / 'monitor.jsonl', RUN / 'timed-traces/trace-schedule.json',
        RUN / 'independent-memory-guard/result.json',
        RUN / 'independent-memory-guard/samples.jsonl',
        ROOT / 'results/collector-partial-trace-audit.json',
        ROOT / 'results/S5V3FFR-COLLECTOR-20261013-source-manifest.json',
        ROOT / 'results/S5V3FFR-COLLECTOR-20261013-worker2-archive-proof.json',
        ROOT / 'results/S5V3FFR-COLLECTOR-20261013-worker3-archive-proof.json',
        WATCH / 'result.json', WATCH / 'samples.jsonl',
        ROOT / 'results/postrollback-recovery-global-safety.json']
    if any(not path.is_file() for path in sources):
        raise RuntimeError('Collector result evidence not complete')
    run, trace, memory, partial, manifest, recovery, health = [read(path) for path in (
        RUN / 'result.json', RUN / 'timed-traces/trace-schedule.json',
        RUN / 'independent-memory-guard/result.json',
        ROOT / 'results/collector-partial-trace-audit.json',
        ROOT / 'results/S5V3FFR-COLLECTOR-20261013-source-manifest.json',
        WATCH / 'result.json', ROOT / 'results/postrollback-recovery-global-safety.json')]
    proofs = [read(path) for path in sources[8:10]]
    if any(p['status'] != 'COLLECTOR_DUAL_ARCHIVE_VERIFIED' or p[
            'manifest_sha256'] != sha(sources[7]) for p in proofs):
        raise RuntimeError('Dual worker archive proof invalid')
    rows = [json.loads(line) for line in (RUN /
        'independent-memory-guard/samples.jsonl').read_text().splitlines()]
    counters = summarize_monitor(RUN / 'monitor.jsonl')
    config = json.loads(subprocess.check_output(['/usr/bin/kubectl',
        '--kubeconfig=/etc/kubernetes/admin.conf', '-n', 'otel-demo', 'get',
        'configmap', 'otel-collector', '-o', 'json'], text=True, timeout=30))
    original = yaml.safe_load(config['data']['relay'])['processors'][
        'memory_limiter']['spike_limit_percentage'] == 25
    status = ('COLLECTOR_RELIABILITY_INCONCLUSIVE_TIMED_CAPTURE_FAIL'
        if run['status'] == 'COLLECTOR_STUDY_FAILED_CLOSED' and trace[
            'status'] == 'TIMED_CAPTURE_FAIL' and recovery[
            'status'] == 'POSTROLLBACK_FRESH_10MIN_OBSERVED' and health[
            'status'] == 'GLOBAL_SAFETY_PASS' and original else
            'COLLECTOR_SAFETY_OR_RECOVERY_BLOCKED')
    value = {'status': status, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'run_id': RUN.name, 'candidate': 'spike_limit_percentage 25 -> 15',
        'original_configuration_restored': original,
        'monitor_samples': run['monitor_samples'],
        'COLLECTOR_REFUSAL_OBSERVED': {'receiver_grpc_delta': counters[
            'receiver_refused_grpc']['delta'], 'limiter_delta': counters[
            'limiter_refused']['delta'], 'limiter_status': counters[
            'limiter_refused']['status']},
        'collector_counters': counters,
        'COLLECTOR_FINAL_SPAN_COVERAGE': {'status':
            'COMPLETE_AT_LATE_FIRST_READ_NOT_TIMELY',
            'issued': partial['issued_trace_ids'],
            'proxy_matched': partial['proxy_server_matched_issued_ids'],
            'raw_spans': partial['raw_span_hits']},
        'TIMED_TRACE_COMPLETION': trace['status'],
        'timed_failure_reason': trace.get('error'),
        'BUSINESS_HEALTH': {'state_machine_requests': run[
            'workload_manifests']['state-machine']['request_count'],
            'request_success_fraction': run['workload_manifests'][
                'state-machine']['request_success_fraction'],
            'image_successful': run['workload_manifests']['controls'][
                'streams']['image_asset']['successful_requests'],
            'cart_successful': run['workload_manifests']['controls'][
                'streams']['cart_view']['successful_requests']},
        'GLOBAL_SAFETY': health['status'],
        'independent_memory_guard_status': memory['status'],
        'independent_memory_peak_bytes': max(row['working_set_bytes'] for row in rows),
        'postrollback_recovery_10min': recovery['status'],
        'postrollback_metric_gap_observed': True,
        'archive_source_manifest_sha256': sha(sources[7]),
        'archive_bytes': manifest['bytes'], 'archive_files': manifest['file_count'],
        'worker_archive_proofs': {p['worker']: p['status'] for p in proofs},
        'source_sha256': {str(path): sha(path) for path in sources},
        'fault_injected': False, 'formal_case': False,
        'new_clean_gate_approved': False}
    save_new(ROOT / 'results/collector-reliability.json', value)
    print(json.dumps({'status': status,
        'receiver_refused': value['COLLECTOR_REFUSAL_OBSERVED']['receiver_grpc_delta'],
        'timed': value['TIMED_TRACE_COMPLETION'],
        'original_restored': original}))


if __name__ == '__main__': main()
