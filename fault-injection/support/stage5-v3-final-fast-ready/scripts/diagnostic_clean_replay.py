#!/usr/bin/env python3
"""Post-run read-only diagnostic; never replaces the source-locked failed scorer."""

from collections import defaultdict
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess

from clean_protocol import assert_disk, save_new, sha
from assess_clean_run import check_probe


OLD_CPU = Path('/root/stage5-v3-autonomous-preparation/scripts/assess_resource_pressure.py')
CONTROLS = Path('/root/stage5-v3-clean-gate-v2/scripts/evaluate_historical_controls.py')
TRACE = Path('/root/stage5-v3-clean-gate-v2/scripts/evaluate_trace_v2.py')
TRACE_IDENTITY = Path('/root/stage5-v3-clean-gate-v2/scripts/trace_identity.py')
NETWORK = Path('/root/stage5-v3-final-fast-ready/scripts/analyze_network_controls.py')
RUN = Path('/data/stage5-v3/final-fast-ready/runs/S5V3FFR-CLEAN-20261013-S1')
PREREG = Path('/root/stage5-v3-final-fast-ready/design/clean-prereg-s1.json')
FAILURE = Path('/root/stage5-v3-final-fast-ready/results/clean-s1-original-scoring-failure.json')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def before_business_coverage(run, start, expected_uids):
    spans = {key: row[1] for key, row in load(TRACE_IDENTITY,
        'trace_identity').span_map(run / 'capture-pipeline/trace-final/before').items()}
    rec = {uid: [set() for _ in range(10)] for uid in expected_uids}
    cart_edge = [set() for _ in range(10)]
    for span in spans.values():
        if (span.get('tag') or {}).get('span@kind') != 'server': continue
        minute = int((span['startTime'] / 1_000_000 - start) // 60)
        if not 0 <= minute < 10: continue
        service = span['process']['serviceName']
        if service == 'recommendation':
            uid = {r.get('key'): r.get('value') for r in span[
                'process'].get('tags') or []}.get('k8s.pod.uid')
            if uid in rec: rec[uid][minute].add(span['traceID'])
        elif service == 'cart':
            for ref in span.get('references') or []:
                parent = spans.get((ref.get('traceID', '').lower(),
                                    ref.get('spanID', '').lower()))
                if parent and parent['process']['serviceName'] == 'checkout' and (
                        parent.get('tag') or {}).get('span@kind') == 'client':
                    cart_edge[minute].add(span['traceID'])
                    break
    rec_counts = {uid: [len(bucket) for bucket in minutes]
                  for uid, minutes in rec.items()}
    edge_counts = [len(bucket) for bucket in cart_edge]
    return {'recommendation_server_trace_ids_by_uid_before_minute': rec_counts,
        'checkout_to_cart_edge_trace_ids_before_minute': edge_counts,
        'recommendation_last_five_before_30_each': len(rec_counts) == 2 and all(
            min(values[5:10]) >= 30 for values in rec_counts.values()),
        'checkout_cart_last_five_before_30_each': min(edge_counts[5:10]) >= 30}


def main():
    output = RUN / 'diagnostic-replay-v1'
    if output.exists() or not FAILURE.is_file():
        raise RuntimeError('Diagnostic replay already exists or original failure not preserved')
    assert_disk(output, 5 * 1024 ** 3)
    output.mkdir()
    schedule = json.loads((RUN / 'schedule.json').read_text())
    pipeline = json.loads((RUN / 'capture-pipeline/pipeline-result.json').read_text())
    capture = {}
    for phase, window in schedule['phase_windows'].items():
        root = RUN / 'capture-pipeline/captures' / phase
        manifest = json.loads((root / 'capture-manifest.json').read_text())
        actual = manifest['window']
        if (manifest['phase'] != phase or manifest['trace_source'] !=
            'JAEGER_OPENSEARCH_STORAGE' or actual['start_utc'] != window[
            'start_utc'] or actual['end_utc'] != window['end_utc'] or
            manifest['trace_records'] <= 0 or manifest['log_records'] <= 0):
            raise RuntimeError('Diagnostic capture time/source mismatch')
        for name, digest in manifest['sha256'].items():
            if sha(root / name) != digest:
                raise RuntimeError('Original capture SHA mismatch: ' + phase + '/' + name)
        capture[phase] = {'status': 'CAPTURE_SOURCE_VERIFIED',
            'manifest_sha256': sha(root / 'capture-manifest.json'),
            'extra_window_keys_accepted_in_diagnostic_parser': sorted(
                set(actual) - {'start_utc', 'end_utc'}),
            'trace_records': manifest['trace_records'],
            'span_records': manifest['span_records'],
            'log_records': manifest['log_records']}
    probes = {phase: check_probe(RUN / 'probes' / phase, count)
              for phase, count in (('before', 120), ('during', 60), ('after', 120))}
    cpu_cmd = ['/usr/bin/python3', '-B', str(OLD_CPU), '--run', str(RUN),
               '--plan', str(PREREG), '--output', str(output / 'old-cpu')]
    cpu_process = subprocess.run(cpu_cmd, capture_output=True, text=True, timeout=120)
    if cpu_process.returncode:
        raise RuntimeError('Old CPU assessor failed: ' + cpu_process.stderr[-500:])
    old_cpu = json.loads((output / 'old-cpu/decision.json').read_text())
    control_mod = load(CONTROLS, 'control_candidates')
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    controls = {}
    for name in ('image_asset', 'cart_view'):
        path = RUN / 'load_pair/controls' / (name + '.jsonl')
        control_manifest = {'files': {str(path.relative_to(RUN)): {
            'sha256': sha(path)}}}
        controls[name] = control_mod.assess_control(path, RUN, control_manifest, start)
    rules = {rule: all(row['rules'].get(rule, False) for row in controls.values())
             for rule in ('A_old', 'B_persistence_peak', 'E_B_and_window')}
    trace_cmd = ['/usr/bin/python3', '-B', str(TRACE), '--run', str(RUN),
                 '--output', str(output / 'trace-v2.json')]
    trace_process = subprocess.run(trace_cmd, capture_output=True, text=True, timeout=240)
    trace = json.loads((output / 'trace-v2.json').read_text()) if trace_process.returncode == 0 \
        else {'status': 'TRACE_V2_DIAGNOSTIC_FAILED', 'stderr': trace_process.stderr[-500:]}
    network_cmd = ['/usr/bin/python3', '-B', str(NETWORK), '--run', str(RUN),
                   '--output', str(output / 'network-controls.json')]
    network_process = subprocess.run(network_cmd, capture_output=True, text=True,
                                     timeout=240)
    network = json.loads((output / 'network-controls.json').read_text()) \
        if network_process.returncode == 0 else {'status': 'NETWORK_DIAGNOSTIC_FAILED',
                                                  'stderr': network_process.stderr[-500:]}
    lock = json.loads((RUN / 'source-lock.json').read_text())
    workload = before_business_coverage(RUN, start, lock['pod_uids']['recommendation'])
    cpu_shadow = json.loads((RUN / 'cpu-safety/manifest.json').read_text())
    old_checks = {'pipeline': pipeline['status'] == 'CAPTURE_PIPELINE_PASS',
        'live_captures': len(capture) == 3,
        'probes': all(p['complete'] for p in probes.values()),
        'worker3_cpu_increment': old_cpu['status'] == 'RESOURCE_PRESSURE_PASS',
        'A_old_client_latency': rules['A_old'],
        'fixed_control_edge_spans_30_every_minute': network.get('status') ==
            'NETWORK_CONTROLS_FULL_WINDOW_AUDITED' and all(network[name][
                'per_minute_30_spans_pass'] for name in ('image', 'cart')),
        'recommendation_replica_before_coverage': workload[
            'recommendation_last_five_before_30_each'],
        'checkout_cart_before_coverage': workload[
            'checkout_cart_last_five_before_30_each']}
    value = {'status': 'POSTRUN_DIAGNOSTIC_REPLAY_NOT_PREREGISTERED_SCORE',
        'run_id': RUN.name, 'at_utc': datetime.now(timezone.utc).isoformat(),
        'original_failure_sha256': sha(FAILURE),
        'old_rule_checks_descriptive': old_checks,
        'old_rule_all_checks_would_pass': all(old_checks.values()),
        'old_cpu': old_cpu, 'client_controls': controls,
        'candidate_shadow_only_not_approved': {
            'CPU_SAFETY_V2': cpu_shadow['status'],
            'TRACE_COMPLETION_V2': trace['status'],
            'CLEAN_CONTROL_B': rules['B_persistence_peak'],
            'CLEAN_CONTROL_E': rules['E_B_and_window']},
        'network_controls': network, 'workload_readiness': workload,
        'probes': probes, 'capture': capture, 'trace_v2': trace,
        'source_sha256': {str(path): sha(path) for path in
            (Path(__file__), OLD_CPU, CONTROLS, TRACE, TRACE_IDENTITY,
             NETWORK, PREREG, FAILURE, RUN / 'schedule.json')},
        'formal_clean_decision_replaced': False,
        'second_seed_allowed': False, 'fault_injected': False}
    save_new(output / 'decision.json', value)
    print(json.dumps({'status': value['status'], 'old_checks': old_checks,
        'candidate_shadow': value['candidate_shadow_only_not_approved']}))


if __name__ == '__main__': main()
