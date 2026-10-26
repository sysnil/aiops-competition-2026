"""Read-only qualification summary from one complete rehearsal archive."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


PHASES = ('before', 'during', 'after')
CONTROLS = ('image_asset', 'cart_view')
SERVICES = ('ad', 'frontend', 'recommendation', 'payment', 'checkout',
            'cart', 'shipping', 'quote')
EDGES = ('checkout->cart', 'checkout->shipping',
         'frontend-proxy->image-provider', 'frontend->cart',
         'frontend->product-catalog')


def read(path):
    return json.loads(path.read_text())


def lines(path):
    for raw in path.read_text().splitlines():
        if raw:
            yield json.loads(raw)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * fraction)] if ordered else None


def minute(stamp, start):
    if isinstance(stamp, str):
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp()
    return int((stamp - start) // 60)


def uid(span):
    return next((tag.get('value') for tag in span.get('process', {}).get('tags', [])
                 if tag.get('key') == 'k8s.pod.uid'), None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing to overwrite a clean qualification result')
    schedule = read(args.run / 'load_pair/schedule.json')
    start = datetime.fromisoformat(schedule['scheduled_start_utc']).timestamp()
    if (round(datetime.fromisoformat(schedule['before_end_utc']).timestamp() - start) != 600 or
            round(datetime.fromisoformat(schedule['during_end_utc']).timestamp() - start) != 900 or
            round(datetime.fromisoformat(schedule['scheduled_end_utc']).timestamp() - start) != 1500):
        raise ValueError('Archive does not have 10+5+10 minute boundaries')
    load = read(args.run / 'load_pair/result.json')
    lifecycle = read(args.run / 'result.json')
    pipeline = read(args.run / 'capture-pipeline/pipeline-result.json')
    route = read(args.run / 'route-proof.json')
    if route['status'] != 'ROUTE_FLOW_PROOF_PASS':
        raise ValueError('Current route/packet proof is missing')

    requests = defaultdict(lambda: defaultdict(lambda: {'success': 0, 'attempted': 0,
                                                        'latency_ms': []}))
    issued = set()
    for session in lines(args.run / 'load_pair/state-machine/sessions.jsonl'):
        for step in session['steps']:
            trace_id = step.get('trace_id')
            if trace_id:
                issued.add(trace_id)
            m = minute(step['time_utc'], start)
            if 0 <= m < 25:
                row = requests['state_machine/' + step['operation']][m]
                row['attempted'] += 1
                row['success'] += bool(step.get('success'))
                if step.get('success') and isinstance(step.get('latency_ms'), (int, float)):
                    row['latency_ms'].append(step['latency_ms'])
    control_ids = defaultdict(set)
    for control in CONTROLS:
        for row in lines(args.run / 'load_pair/controls' / (control + '.jsonl')):
            m = minute(row.get('time_utc', row.get('scheduled_utc')), start)
            if 0 <= m < 25:
                item = requests['control/' + control][m]
                item['attempted'] += 1
                item['success'] += bool(row.get('success'))
                if row.get('success') and isinstance(row.get('latency_ms'), (int, float)):
                    item['latency_ms'].append(row['latency_ms'])
            if row.get('trace_id'):
                issued.add(row['trace_id'])
                control_ids[control].add(row['trace_id'])

    pods_by_phase = {}
    source_health = {}
    metric_samples = {}
    by_trace = defaultdict(dict)
    for phase in PHASES:
        capture = args.run / 'capture-pipeline/captures' / phase
        manifest = read(capture / 'capture-manifest.json')
        source_health[phase] = {
            'trace_source': manifest['trace_source'],
            'span_records': manifest['span_records'],
            'log_records': manifest['log_records'],
            'start_lag_seconds': manifest['start_lag_seconds'],
            'completion_lag_seconds': manifest['completion_lag_seconds'],
        }
        completion = args.run / 'capture-pipeline/trace-completion' / phase
        completion_manifest = read(completion / 'completion-manifest.json')
        completed_source = read(completion / 'trace-storage-source.json')
        first_hash = hashlib.sha256((capture / 'capture-manifest.json').read_bytes()).hexdigest()
        if (completion_manifest['first_capture_sha256'] != first_hash or
                completion_manifest['first_span_count'] != manifest['span_records'] or
                completion_manifest['supplement_span_count'] < manifest['span_records'] or
                completion_manifest['original_capture_overwritten'] or
                completion_manifest['retrospective'] or
                not 60 <= completion_manifest['start_lag_seconds'] <= 120 or
                completed_source['trace_source'] != manifest['trace_source'] or
                completed_source['unique_spans'] != completion_manifest['supplement_span_count'] or
                completed_source['query_start_utc'] != manifest['window']['start_utc'] or
                completed_source['query_end_utc'] != manifest['window']['end_utc']):
            raise ValueError('Immutable +60 second trace completion cannot be verified')
        source_health[phase]['late_span_count'] = completion_manifest['late_span_count']
        source_health[phase]['completed_span_records'] = completed_source['unique_spans']
        source_health[phase]['completion_manifest_sha256'] = hashlib.sha256(
            (completion / 'completion-manifest.json').read_bytes()).hexdigest()
        pods = read(capture / 'kubernetes/pods.json')['items']
        pods_by_phase[phase] = {p['metadata']['uid']: {
            'service': p['metadata']['labels'].get('opentelemetry.io/name'),
            'name': p['metadata']['name'], 'node': p['spec']['nodeName']}
            for p in pods if not p['metadata'].get('deletionTimestamp')}
        metric_samples[phase] = {}
        for family in ('node_cpu', 'node_cpu_time', 'node_ready', 'node_memory',
                       'cpu', 'memory',
                       'cfs_periods', 'cfs_throttled_periods', 'cfs_throttled_seconds'):
            metric_path = capture / 'metrics' / (family + '.json')
            if not metric_path.is_file() and family == 'node_ready':
                metric_samples[phase][family] = []
                continue
            series = read(metric_path)['data']['result']
            selected = [row for row in series if (
                row['metric'].get('k8s_node_name') == 'k8s-worker1'
                if family.startswith('node_') else
                row['metric'].get('pod', '').startswith('recommendation-'))]
            metric_samples[phase][family] = [{
                'pod': row['metric'].get('pod'),
                'node': row['metric'].get('k8s_node_name'),
                'samples': len(row['values'])} for row in selected]
        for page in sorted((capture / 'trace-storage-pages').glob('page-*.json')):
            relative = str(page.relative_to(capture))
            if hashlib.sha256(page.read_bytes()).hexdigest() != manifest['sha256'][relative]:
                raise ValueError('Captured trace page hash differs from immutable manifest')
        completed_pages = sorted((completion / 'trace-storage-pages').glob('page-*.json'))
        if len(completed_pages) != completed_source['raw_pages']:
            raise ValueError('Completed trace page count differs from storage manifest')
        completed_hashes = []
        for page in completed_pages:
            completed_hashes.append(hashlib.sha256(page.read_bytes()).hexdigest())
            for hit in read(page)['hits']['hits']:
                span = hit['_source']
                if span['traceID'] not in issued:
                    continue
                trace = by_trace[span['traceID']]
                if span['spanID'] in trace:
                    raise ValueError('Duplicate generated trace/span in captured windows')
                trace[span['spanID']] = span
        source_health[phase]['completed_pages_chain_sha256'] = hashlib.sha256(
            '\n'.join(completed_hashes).encode()).hexdigest()

    service_bins = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    server_without_uid = defaultdict(lambda: defaultdict(set))
    server_without_uid_durations = defaultdict(lambda: defaultdict(list))
    service_durations = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    edge_bins = defaultdict(lambda: defaultdict(set))
    edge_client_durations = defaultdict(lambda: defaultdict(list))
    control_edge_bins = defaultdict(lambda: defaultdict(set))
    missing_uid = Counter()
    mismatched_uid = Counter()
    missing_parents = 0
    for trace_id, spans in by_trace.items():
        for span in spans.values():
            service = span.get('process', {}).get('serviceName')
            kind = (span.get('tag') or {}).get('span@kind')
            m = minute(span['startTimeMillis'] / 1000, start)
            if not 0 <= m < 25:
                continue
            pod_uid = uid(span)
            if service in SERVICES and kind == 'server':
                if not pod_uid:
                    missing_uid[service] += 1
                    server_without_uid[service][m].add(trace_id)
                    server_without_uid_durations[service][m].append(
                        span['duration'] / 1000)
                elif any(pods_by_phase[phase].get(pod_uid, {}).get('service') == service
                         for phase in PHASES):
                    service_bins[service][pod_uid][m].add(trace_id)
                    service_durations[service][pod_uid][m].append(
                        span['duration'] / 1000)
                else:
                    mismatched_uid[service] += 1
            if kind != 'server':
                continue
            for ref in span.get('references') or []:
                parent = spans.get(ref.get('spanID'))
                if not parent:
                    missing_parents += 1
                    continue
                src = parent.get('process', {}).get('serviceName')
                edge = src + '->' + service if src else None
                if edge not in EDGES:
                    continue
                edge_bins[edge][m].add(trace_id)
                edge_client_durations[edge][m].append(parent['duration'] / 1000)
                parent_uid = uid(parent)
                if edge == 'frontend->cart' and parent_uid:
                    edge_bins[edge + '/' + parent_uid][m].add(trace_id)
                for control, ids in control_ids.items():
                    if trace_id in ids:
                        control_edge_bins[control + '/' + edge][m].add(trace_id)
                break

    probes = {}
    for phase, planned in (('before', 120), ('during', 60), ('after', 120)):
        source = args.run / ('probe-' + phase)
        manifest = read(source / 'manifest.json')
        rows = list(lines(source / 'observations.jsonl'))
        if manifest['planned_samples'] != planned or len(rows) != planned:
            raise ValueError('Probe schedule omitted planned points')
        counts = Counter(row['status'] for row in rows)
        icmp_losses = [row['icmp']['loss_fraction'] for row in rows
                       if row['icmp'].get('loss_fraction') is not None]
        longest = active = 0
        for row in rows:
            active = 0 if row['status'] == 'SUCCESS' else active + 1
            longest = max(longest, active)
        probes[phase] = {'planned': planned, 'valid_numeric': counts['SUCCESS'],
                         'status_counts': dict(counts),
                         'max_consecutive_invalid': longest,
                         'receiver_loss_median': percentile([
                             row['iperf3']['loss_fraction'] for row in rows
                             if row['status'] == 'SUCCESS'], .50),
                         'throughput_bps_median': percentile([
                             row['iperf3']['throughput_bps'] for row in rows
                             if row['status'] == 'SUCCESS'], .50),
                         'icmp_loss_median': percentile(icmp_losses, .50),
                         'icmp_loss_mean': (sum(icmp_losses) / len(icmp_losses)
                                            if icmp_losses else None),
                         'icmp_valid_samples': len(icmp_losses),
                         'icmp_rtt_median_ms': percentile([
                             row['icmp']['rtt_median_ms'] for row in rows
                             if row['icmp'].get('rtt_median_ms') is not None], .50)}

    control_summary = {}
    for control in CONTROLS:
        rows = requests['control/' + control]
        baseline = percentile([p95 for m in range(10)
                               if (p95 := percentile(rows[m]['latency_ms'], .95))
                               is not None], .50)
        summary = []
        for m in range(25):
            row = rows[m]
            p95 = percentile(row['latency_ms'], .95)
            summary.append({'minute': m, 'attempted': row['attempted'],
                            'successful': row['success'], 'client_p95_ms': p95,
                            'client_p95_to_clean_median': p95 / baseline if p95 is not None
                            and baseline else None})
        control_summary[control] = summary

    result = {
        'status': 'ARCHIVE_ANALYSIS_COMPLETE', 'run_id': schedule['run_id'],
        'fault_injected': bool(lifecycle.get('fault_start_utc')),
        'formal_case_created': lifecycle.get('formal_case_created', False),
        'source': 'immutable_live_capture_plus_state_machine_and_fixed_business_requests',
        'schedule': schedule, 'load_status': load['status'],
        'lifecycle_status': lifecycle['status'], 'pipeline_status': pipeline['status'],
        'route_proof_status': route['status'], 'route_proof_sha256': hashlib.sha256(
            (args.run / 'route-proof.json').read_bytes()).hexdigest(),
        'source_health': source_health, 'pod_identity_by_phase': pods_by_phase,
        'issued_trace_ids': len(issued), 'matched_trace_ids': len(by_trace),
        'missing_parent_relations': missing_parents,
        'target_metric_samples': metric_samples,
        'missing_server_pod_uid': dict(missing_uid),
        'server_trace_ids_without_uid_by_service_and_minute': {
            service: [len(bins[m]) for m in range(25)]
            for service, bins in server_without_uid.items()},
        'server_p95_ms_without_uid_by_service_and_minute': {
            service: [percentile(bins[m], .95) for m in range(25)]
            for service, bins in server_without_uid_durations.items()},
        'mismatched_server_pod_uid': dict(mismatched_uid),
        'service_server_trace_ids_by_pod_and_minute': {
            service: {pod: [len(minute_bins[m]) for m in range(25)]
                      for pod, minute_bins in by_pod.items()}
            for service, by_pod in service_bins.items()},
        'service_server_p95_ms_by_pod_and_minute': {
            service: {pod: [percentile(minute_bins[m], .95) for m in range(25)]
                      for pod, minute_bins in by_pod.items()}
            for service, by_pod in service_durations.items()},
        'call_edge_trace_ids_per_minute': {
            edge: [len(bins[m]) for m in range(25)]
            for edge, bins in edge_bins.items()},
        'call_edge_client_p95_ms_per_minute': {
            edge: [percentile(bins[m], .95) for m in range(25)]
            for edge, bins in edge_client_durations.items()},
        'fixed_control_edge_trace_ids_per_minute': {
            edge: [len(bins[m]) for m in range(25)]
            for edge, bins in control_edge_bins.items()},
        'fixed_control_client': control_summary,
        'state_machine_client': {
            operation: [{'minute': m, 'attempted': bins[m]['attempted'],
                         'successful': bins[m]['success'],
                         'client_p95_ms': percentile(bins[m]['latency_ms'], .95)}
                        for m in range(25)]
            for operation, bins in requests.items()
            if operation.startswith('state_machine/')},
        'probes': probes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'status': result['status'], 'matched_traces': len(by_trace),
                      'route': result['route_proof_status'], 'probe_valid': {
                          phase: row['valid_numeric'] for phase, row in probes.items()}}))


if __name__ == '__main__':
    main()
