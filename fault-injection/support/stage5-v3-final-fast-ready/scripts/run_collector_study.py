#!/usr/bin/env python3
"""Single 30-minute no-fault candidate study with independent timer fallback."""

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request

import yaml

from clean_protocol import DATA_ROOT, assert_disk, save_new, sha
from collector_candidate import old_module, TIMER
from run_clean import (BASE_URL, CONTROLS, MACHINE, PEOPLE, PROFILES, STATE,
                       KUBECTL, current_snapshot, validate_workload)


PROM = 'http://10.97.14.138:9090'
METRICS = {
    'receiver_accepted_grpc': 'otelcol_receiver_accepted_spans_total{receiver="otlp",transport="grpc"}',
    'receiver_refused_grpc': 'otelcol_receiver_refused_spans_total{receiver="otlp",transport="grpc"}',
    'limiter_refused': 'otelcol_processor_memory_limiter_refused_spans_total{processor="memory_limiter"}',
    'jaeger_sent': 'otelcol_exporter_sent_spans_total{exporter="otlp_grpc/jaeger"}',
    'jaeger_send_failed': 'otelcol_exporter_send_failed_spans_total{exporter="otlp_grpc/jaeger"}',
    'jaeger_queue_size': 'otelcol_exporter_queue_size{exporter="otlp_grpc/jaeger",data_type="traces"}',
    'heap_bytes': 'otelcol_process_runtime_heap_alloc_bytes',
}
COUNTERS = ('receiver_accepted_grpc', 'receiver_refused_grpc',
            'limiter_refused', 'jaeger_sent', 'jaeger_send_failed')


def scalar(query, pod):
    expression = query[:-1] + ',k8s_pod_name="' + pod + '"}' if query.endswith('}') \
        else query + '{k8s_pod_name="' + pod + '"}'
    url = PROM + '/api/v1/query?' + urllib.parse.urlencode({'query': expression})
    with urllib.request.urlopen(url, timeout=15) as response:
        data = json.load(response)
    rows = data['data']['result'] if data['status'] == 'success' else []
    if not rows:
        return {'status': 'UNKNOWN_NOT_ZERO', 'value': None}
    if len(rows) != 1:
        return {'status': 'AMBIGUOUS', 'value': None, 'series_count': len(rows)}
    return {'status': 'OBSERVED', 'value': float(rows[0]['value'][1]),
            'sample_timestamp': float(rows[0]['value'][0])}


def observe(pod):
    values = {name: scalar(query, pod) for name, query in METRICS.items()}
    values['working_set_bytes'] = scalar(
        'container_memory_working_set_bytes{namespace="otel-demo",container="opentelemetry-collector"}',
        pod)
    return values


def infrastructure():
    nodes = json.loads(subprocess.check_output(KUBECTL + ['get', 'nodes', '-o',
        'json'], text=True, timeout=30))['items']
    if len(nodes) != 4 or any({c['type']: c['status'] for c in node['status'][
            'conditions']}.get('Ready') != 'True' for node in nodes):
        raise RuntimeError('Four-node health changed')
    with urllib.request.urlopen('http://10.111.114.255:9200/_cluster/health',
                               timeout=10) as response:
        if json.load(response)['status'] != 'green':
            raise RuntimeError('OpenSearch not green')
    with urllib.request.urlopen(PROM + '/-/ready', timeout=10) as response:
        if response.status != 200:
            raise RuntimeError('Prometheus not Ready')
    return {'nodes_ready': 4, 'opensearch': 'green', 'prometheus': 'ready'}


def summarize_monitor(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    metrics = {}
    for name in COUNTERS:
        samples = [row['metrics'][name] for row in rows]
        present = [row['value'] for row in samples if row['status'] == 'OBSERVED']
        resets = sum(a < b for a, b in zip(present[1:], present[:-1]))
        metrics[name] = {'status': 'OBSERVED' if len(present) == len(rows) else
            'UNKNOWN_MISSING_OR_AMBIGUOUS', 'first': present[0] if present else None,
            'last': present[-1] if present else None, 'resets': resets,
            'delta': present[-1] - present[0] if len(present) == len(rows) and
            not resets else None}
    return metrics


def validate_prereg(plan):
    if (plan.get('status') != 'PREREGISTERED_COLLECTOR_SINGLE_CANDIDATE' or
        plan.get('id') != 'S5V3FFR-COLLECTOR-20261013' or
        plan.get('duration_seconds') != 1800 or plan.get('seed') != 864001 or
        plan.get('fault_injected') is not False or
        plan.get('candidate_spike_limit_percentage') != 15 or
        plan.get('original_spike_limit_percentage') != 25 or
        plan.get('data_root') != str(DATA_ROOT) or
        plan.get('planned_raw_bytes', 0) < 5 * 1024 ** 3):
        raise ValueError('Collector plan changed or second candidate requested')
    validate_workload(plan)


def fresh_health(path):
    health = json.loads(path.read_text())
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(
        health['captured_at_utc'])).total_seconds()
    if health['status'] != 'GLOBAL_SAFETY_PASS' or health.get('blockers') or \
            not 0 <= age <= 180:
        raise RuntimeError('Fresh Global Safety PASS required')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prereg', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--applied', type=Path, required=True)
    parser.add_argument('--health', type=Path, required=True)
    args = parser.parse_args()
    prereg = yaml.safe_load(args.prereg.read_text())
    validate_prereg(prereg)
    run = DATA_ROOT / 'runs' / prereg['id']
    if run.exists():
        raise ValueError('One immutable Collector run only')
    guard = assert_disk(run, prereg['planned_raw_bytes'])
    fresh_health(args.health)
    original = old_module()
    plan, applied = json.loads(args.plan.read_text()), json.loads(args.applied.read_text())
    if plan['status'] != 'SINGLE_COLLECTOR_CANDIDATE_PLANNED_NOT_APPLIED' or \
            applied['status'] != 'COLLECTOR_CANDIDATE_APPLIED' or \
            not original.timer_active():
        raise RuntimeError('Candidate not applied or independent rollback timer unavailable')
    gateway = original.verify_live(plan, True)
    snapshot = current_snapshot('collector-study-route')
    if snapshot['collector_config_sha256'] != plan['new_relay_sha256']:
        raise RuntimeError('Collector source lock mismatch')
    infrastructure()
    run.mkdir(parents=True)
    start = datetime.now(timezone.utc) + timedelta(seconds=60)
    end = start + timedelta(seconds=1800)
    sources = (args.prereg, args.plan, args.applied, args.health, Path(__file__),
               Path(__file__).with_name('timed_collector_traces.py'),
               STATE, CONTROLS, MACHINE, PROFILES, PEOPLE)
    schedule = {'id': prereg['id'], 'seed': prereg['seed'],
        'duration_seconds': 1800, 'scheduled_start_utc': start.isoformat(),
        'scheduled_end_utc': end.isoformat(), 'gateway': gateway,
        'pod_uids': snapshot['pod_uids'], 'node_placement': snapshot['node_placement'],
        'source_sha256': {str(path): sha(path) for path in sources},
        'collector_relay_sha256': plan['new_relay_sha256'],
        'data_disk_guard': guard, 'fault_injected': False, 'formal_case': False}
    save_new(run / 'schedule.json', schedule)
    commands = {
        'state-machine': ['/usr/bin/python3', '-B', str(STATE), '--machine', str(MACHINE),
            '--profiles', str(PROFILES), '--people', str(PEOPLE), '--profile', 'MEDIUM',
            '--seed', str(prereg['seed']), '--duration', '1800', '--start-utc',
            start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'state-machine')],
        'controls': ['/usr/bin/python3', '-B', str(CONTROLS), '--plan', str(args.prereg),
            '--seed', str(prereg['seed']), '--duration', '1800', '--start-utc',
            start.isoformat(), '--base-url', BASE_URL, '--output', str(run / 'controls')],
        'timed-traces': ['/usr/bin/python3', '-B', str(Path(__file__).with_name(
            'timed_collector_traces.py')), '--run', str(run), '--output',
            str(run / 'timed-traces')]}
    processes, handles = {}, []
    result = {'status': 'COLLECTOR_STUDY_FAILED_CLOSED', 'run_id': prereg['id'],
              'fault_injected': False, 'candidate_only': True, 'monitor_samples': 0}
    try:
        for name, command in commands.items():
            stdout, stderr = (run / (name + '.stdout')).open('x'), (run / (name + '.stderr')).open('x')
            handles.extend((stdout, stderr))
            processes[name] = subprocess.Popen(command, stdout=stdout, stderr=stderr)
        with (run / 'monitor.jsonl').open('x') as stream:
            for number in range(120):
                planned = start.timestamp() + number * 15
                time.sleep(max(0, planned - time.time()))
                if time.time() - planned > 5:
                    raise RuntimeError('Monitor schedule missed')
                if any(processes[name].poll() is not None for name in ('state-machine', 'controls')):
                    raise RuntimeError('Workload exited before scheduled end')
                disk = assert_disk(run, prereg['planned_raw_bytes'])
                metrics = observe(gateway['pod_name'])
                if any(metrics[name]['status'] != 'OBSERVED' for name in
                       ('receiver_accepted_grpc', 'receiver_refused_grpc', 'jaeger_sent')):
                    raise RuntimeError('Critical Collector telemetry unavailable')
                live = original.gateway_pod()
                if live['metadata']['uid'] != gateway['pod_uid'] or \
                        live['status']['containerStatuses'][0]['restartCount'] != 0:
                    raise RuntimeError('Collector restarted or changed')
                if number % 4 == 0:
                    infrastructure()
                    now = current_snapshot('collector-study-route')
                    if now['pod_uids'] != snapshot['pod_uids'] or \
                            now['live_route_sha256'] != snapshot['live_route_sha256'] or \
                            now['collector_config_sha256'] != plan['new_relay_sha256']:
                        raise RuntimeError('Business, route, or Collector source drift')
                working = metrics['working_set_bytes']['value']
                if working is not None and working >= 380 * 1024 ** 2:
                    raise RuntimeError('Collector working set breached preregistered stop')
                row = {'planned_utc': datetime.fromtimestamp(planned,
                    timezone.utc).isoformat(), 'observed_utc': datetime.now(
                    timezone.utc).isoformat(), 'collector': gateway['pod_name'],
                    'metrics': metrics, 'root_free_bytes': disk['root_free_bytes'],
                    'data_free_bytes': disk['data_free_bytes']}
                stream.write(json.dumps(row) + '\n'); stream.flush()
                result['monitor_samples'] += 1
        codes = {name: processes[name].wait(timeout=90) for name in ('state-machine', 'controls')}
        if any(codes.values()):
            raise RuntimeError('Workload process failed')
        result['workload_manifests'] = {name: json.loads((run / name /
            'result-manifest.json').read_text()) for name in ('state-machine', 'controls')}
        if any(row['status'] not in ('LOAD_PASS', 'CONTROL_STREAMS_COMPLETE')
               for row in result['workload_manifests'].values()):
            raise RuntimeError('No-fault workload generated failures')
        trace_code = processes['timed-traces'].wait(timeout=320)
        trace = json.loads((run / 'timed-traces/trace-schedule.json').read_text())
        result['timed_traces'] = trace
        if trace_code or trace['status'] != 'TIMED_TRACE_CAPTURE_PASS':
            raise RuntimeError('Timed Trace Completion failed')
        scoring = trace['stages']['scoring_plus120']
        if scoring['proxy_server_issued_trace_ids'] != scoring['issued_trace_ids']:
            raise RuntimeError('Critical issued-to-Proxy Trace gap at scoring deadline')
        result['collector_counters'] = summarize_monitor(run / 'monitor.jsonl')
        result['COLLECTOR_REFUSAL_OBSERVED'] = {
            name: result['collector_counters'][name]['delta'] for name in
            ('receiver_refused_grpc', 'limiter_refused')}
        result['COLLECTOR_FINAL_SPAN_COVERAGE'] = {
            'issued': scoring['issued_trace_ids'],
            'proxy_matched': scoring['proxy_server_issued_trace_ids']}
        result['TIMED_TRACE_COMPLETION'] = trace['status']
        result['BUSINESS_HEALTH'] = 'PASS'
        result['GLOBAL_SAFETY'] = 'PASS_DURING_MONITORED_WINDOW'
        result['status'] = 'COLLECTOR_CANDIDATE_NO_FAULT_OBSERVATION_COMPLETE'
    except Exception as error:
        result['error'] = str(error)
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill()
        for handle in handles: handle.close()
        try:
            restored = original.rollback(plan, run / 'collector-rollback.json')
            result['rollback'] = restored
            subprocess.run(['systemctl', 'stop', TIMER], check=True,
                capture_output=True, text=True, timeout=15)
        except Exception as error:
            result['status'] = 'COLLECTOR_ROLLBACK_FAILED_STOP_ALL'
            result['rollback_error'] = str(error)
        result['completed_utc'] = datetime.now(timezone.utc).isoformat()
        save_new(run / 'result.json', result)
        print(json.dumps({'status': result['status'], 'monitor_samples':
            result['monitor_samples'], 'error': result.get('error'),
            'rollback_error': result.get('rollback_error')}), flush=True)
    if result['status'] != 'COLLECTOR_CANDIDATE_NO_FAULT_OBSERVATION_COMPLETE':
        raise SystemExit(1)


if __name__ == '__main__': main()
