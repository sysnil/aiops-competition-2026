#!/usr/bin/env python3
"""Score old Clean gates and candidate shadow gates from one immutable raw run."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess

from clean_protocol import assert_disk, save_new, sha, validate_capture


OLD = Path('/root/stage5-v3-autonomous-preparation/scripts/assess_resource_pressure.py')
CONTROLS = Path('/root/stage5-v3-clean-gate-v2/scripts/evaluate_historical_controls.py')
TRACE = Path('/root/stage5-v3-clean-gate-v2/scripts/evaluate_trace_v2.py')
NETWORK = Path('/root/stage5-v3-final-fast-ready/scripts/analyze_network_controls.py')


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_probe(root, expected):
    manifest = json.loads((root / 'manifest.json').read_text())
    rows = [json.loads(line) for line in (root / 'observations.jsonl').read_text().splitlines()]
    if (manifest['status'] != 'QUALIFICATION_PROBES_COMPLETE' or
        manifest['path'] != 'NETWORK_PATH/k8s-worker2/k8s-worker3' or
        manifest['planned_samples'] != expected or len(rows) != expected or
        manifest['observations_sha256'] != sha(root / 'observations.jsonl') or
        any(row['index'] != idx for idx, row in enumerate(rows))):
        raise RuntimeError('Network probe identity, schedule, or SHA mismatch')
    valid = sum(row['status'] == 'SUCCESS' for row in rows)
    longest = current = 0
    for row in rows:
        current = 0 if row['status'] == 'SUCCESS' else current + 1
        longest = max(longest, current)
    return {'planned': expected, 'valid_numeric': valid,
            'max_consecutive_invalid': longest,
            'status_counts': manifest['status_counts'],
            'complete': valid >= (54 if expected == 60 else 108) and longest <= 2}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--prereg', type=Path, required=True)
    args = parser.parse_args()
    run = args.run
    output = run / 'quality-assessment'
    if output.exists() or not (run / 'run-result.json').is_file():
        raise RuntimeError('Quality output exists or raw run unresolved')
    assert_disk(output, 5 * 1024 ** 3)
    raw = json.loads((run / 'run-result.json').read_text())
    if raw['status'] != 'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE':
        raise RuntimeError('Raw run did not complete')
    schedule = json.loads((run / 'schedule.json').read_text())
    pipeline = json.loads((run / 'capture-pipeline/pipeline-result.json').read_text())
    if pipeline['status'] != 'CAPTURE_PIPELINE_PASS':
        raise RuntimeError('Live capture pipeline failed')
    output.mkdir()
    captures = {}
    for phase, window in schedule['phase_windows'].items():
        root = run / 'capture-pipeline/captures' / phase
        manifest = json.loads((root / 'capture-manifest.json').read_text())
        validate_capture(phase, manifest, window['start_utc'], window['end_utc'])
        for relative, digest in manifest['sha256'].items():
            if sha(root / relative) != digest:
                raise RuntimeError('Raw capture SHA mismatch: ' + phase + '/' + relative)
        captures[phase] = {'status': 'LIVE_CAPTURE_PASS',
            'manifest_sha256': sha(root / 'capture-manifest.json'),
            'trace_records': manifest['trace_records'],
            'log_records': manifest['log_records']}
    probes = {phase: check_probe(run / 'probes' / phase, count)
              for phase, count in (('before', 120), ('during', 60), ('after', 120))}
    pressure = subprocess.run(['/usr/bin/python3', '-B', str(OLD), '--run',
        str(run), '--plan', str(args.prereg), '--output', str(output / 'old-cpu')],
        capture_output=True, text=True, timeout=90)
    if pressure.returncode:
        raise RuntimeError('Old CPU reference assessment failed: ' + pressure.stderr[-500:])
    old_cpu = json.loads((output / 'old-cpu/decision.json').read_text())
    control_mod = load(CONTROLS, 'control_rules')
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    controls = {}
    for name in ('image_asset', 'cart_view'):
        path = run / 'load_pair/controls' / (name + '.jsonl')
        root = run
        manifest = {'files': {str(path.relative_to(root)): {'sha256': sha(path)}}}
        controls[name] = control_mod.assess_control(path, root, manifest, start)
    client_rules = {key: all(row['rules'].get(key, False) for row in controls.values())
                    for key in ('A_old', 'B_persistence_peak', 'E_B_and_window')}
    trace_run = subprocess.run(['/usr/bin/python3', '-B', str(TRACE), '--run',
        str(run), '--output', str(output / 'trace-v2.json')], capture_output=True,
        text=True, timeout=180)
    trace = json.loads((output / 'trace-v2.json').read_text()) if trace_run.returncode == 0 else {
        'status': 'TRACE_V2_ASSESSMENT_FAILED', 'error': trace_run.stderr[-500:]}
    network_run = subprocess.run(['/usr/bin/python3', '-B', str(NETWORK), '--run',
        str(run), '--output', str(output / 'network-controls.json')],
        capture_output=True, text=True, timeout=180)
    network = json.loads((output / 'network-controls.json').read_text()) if \
        network_run.returncode == 0 else {'status': 'NETWORK_CONTROL_ANALYSIS_FAILED',
                                           'error': network_run.stderr[-500:]}
    cpu_v2 = json.loads((run / 'cpu-safety/manifest.json').read_text()) if (
        run / 'cpu-safety/manifest.json').is_file() else {'status': 'UNKNOWN'}
    old_gate = (old_cpu['status'] == 'RESOURCE_PRESSURE_PASS' and
                client_rules['A_old'] and all(row['complete'] for row in probes.values())
                and all(row['status'] == 'LIVE_CAPTURE_PASS' for row in captures.values())
                and network.get('status') == 'NETWORK_CONTROLS_FULL_WINDOW_AUDITED'
                and all(network[name]['per_minute_30_spans_pass'] for name in
                        ('image', 'cart')))
    result = {'status': 'CLEAN_QUALITY_ASSESSED', 'run_id': run.name,
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'old_rules': {'status': 'PASS' if old_gate else 'FAIL',
            'worker3_cpu_additional_cores': old_cpu['added_cores']['k8s-worker3'],
            'cpu_limit_cores': 0.25, 'old_cpu_all_nodes': old_cpu,
            'per_minute_p95_ratio_max': 1.25,
            'client_control_A_old': client_rules['A_old']},
        'candidate_shadow_only_not_approved': {
            'CPU_SAFETY_V2': cpu_v2['status'],
            'TRACE_COMPLETION_V2': trace['status'],
            'CLEAN_CONTROL_B': client_rules['B_persistence_peak'],
            'CLEAN_CONTROL_E': client_rules['E_B_and_window']},
        'controls': controls, 'network_controls': network,
        'probes': probes, 'captures': captures,
        'trace_v2': trace, 'fault_injected': False,
        'formal_qualification_allowed': False}
    save_new(output / 'decision.json', result)
    print(json.dumps({'status': result['status'], 'old_gate': result['old_rules']['status'],
        'candidate_shadow': result['candidate_shadow_only_not_approved']}))


if __name__ == '__main__': main()
