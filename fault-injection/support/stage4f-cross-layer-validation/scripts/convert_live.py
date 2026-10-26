#!/usr/bin/env python3
"""Convert fresh public Stage4F captures into one typed, GT-free input schema."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'algorithms'))

from build_candidates import build as build_candidates
from network_path_precheck import validate as validate_path
from pod_precheck import FAMILIES as POD_REQUIRED_METRICS
from signal_rules import (error_log_rate, latency_p95, network_drop_fraction,
                          percentile, resource_metric)
from microrca_xl import run as run_microrca
from simple_rca_calibrated import score_signals
from trace_storage_reader import TRACE_SOURCE


BIN_NS = 15_000_000_000
METRIC_NAMES = {
    'cpu': 'container_cpu_usage_seconds_total',
    'memory': 'container_memory_working_set_bytes',
    'cfs_periods': 'container_cpu_cfs_periods_total',
    'cfs_throttled_periods': 'container_cpu_cfs_throttled_periods_total',
    'cfs_throttled_seconds': 'container_cpu_cfs_throttled_seconds_total',
    'node_cpu': 'k8s_node_cpu_usage',
    'node_cpu_time': 'k8s_node_cpu_time_seconds_total',
    'node_memory': 'k8s_node_memory_available_bytes',
    'span_calls': 'traces_span_metrics_calls_total',
    'http_failures': 'k6_http_req_failed_total',
}
COUNTERS = {'cpu', 'cfs_periods', 'cfs_throttled_periods', 'cfs_throttled_seconds',
            'node_cpu_time', 'span_calls', 'http_failures'}
FORBIDDEN = re.compile(r'(?i)(?:\bVM1\b|gretap|netem|fault[_ -]?target|'
                       r'fault[_ -]?parameter|injection[_ -]?command|root[_ -]?layer|'
                       r'root[_ -]?entity|evaluator_gt|\bF0[1-8]\b)')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, separators=(',', ':')) + '\n')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n')


def load_capture(path, expected_phase):
    manifest = read_json(path / 'capture-manifest.json')
    if manifest.get('schema_version') != 1 or manifest.get('phase') != expected_phase or (
            manifest.get('source') != 'live_prometheus_opensearch_jaeger_storage_kubernetes') or (
            manifest.get('trace_source') != TRACE_SOURCE):
        raise ValueError('Capture identity/source mismatch: ' + str(path))
    actual = {str(file.relative_to(path)) for file in path.rglob('*') if file.is_file()
              and file.name != 'capture-manifest.json'}
    if actual != set(manifest['sha256']):
        raise ValueError('Capture file set differs from manifest')
    for name, expected in manifest['sha256'].items():
        if digest(path / name) != expected:
            raise ValueError('Capture SHA-256 mismatch: ' + name)
    trace_source = read_json(path / 'trace-storage-source.json')
    if (trace_source.get('trace_source') != TRACE_SOURCE or
            trace_source.get('indices') != manifest.get('trace_storage_indices') or
            trace_source.get('query_sha256') != manifest.get('trace_query_sha256') or
            trace_source.get('unique_traces') != manifest.get('trace_records') or
            trace_source.get('unique_spans') != manifest.get('span_records') or
            trace_source.get('query_start_utc') != manifest['window']['start_utc'] or
            trace_source.get('query_end_utc') != manifest['window']['end_utc']):
        raise ValueError('Trace storage provenance differs from capture manifest')
    window = manifest['window']
    if window['start_unix_ns'] >= window['end_unix_ns']:
        raise ValueError('Reversed observation window')
    observed = datetime.fromisoformat(manifest['metadata_observed_utc'])
    ended = datetime.fromisoformat(window['end_utc'])
    if not 0 <= (observed - ended).total_seconds() <= 120:
        raise ValueError('Kubernetes metadata was not captured near window end')
    return {'path': path, 'manifest': manifest, 'window': window,
            'kubernetes': {name: read_json(path / 'kubernetes' / (name + '.json'))
                           for name in ('pods', 'services', 'endpointslices', 'deployments', 'nodes')}}


def validate_windows(captures):
    before, during = captures['before']['window'], captures['during']['window']
    if before['end_unix_ns'] - before['start_unix_ns'] < 600_000_000_000 or (
            during['end_unix_ns'] - during['start_unix_ns'] != 300_000_000_000):
        raise ValueError('Stage4F before/during duration contract failed')
    if before['end_unix_ns'] > during['start_unix_ns']:
        raise ValueError('Before and during windows overlap')
    if 'after' in captures:
        after = captures['after']['window']
        if after['start_unix_ns'] < during['end_unix_ns'] or (
                after['end_unix_ns'] - after['start_unix_ns'] < 600_000_000_000):
            raise ValueError('Recovery window contract failed')


def ready_node(item):
    return any(condition['type'] == 'Ready' and condition['status'] == 'True'
               for condition in item.get('status', {}).get('conditions', []))


def kubernetes_metadata(capture, case_id):
    kube = capture['kubernetes']
    nodes = [{'name': item['metadata']['name'], 'ready': ready_node(item)}
             for item in kube['nodes']['items']]
    ready = {item['name'] for item in nodes if item['ready']}
    services = {item['metadata']['name'] for item in kube['services']['items']
                if item.get('spec', {}).get('selector')}
    pods = []
    for item in kube['pods']['items']:
        labels = item['metadata'].get('labels', {})
        service = labels.get('opentelemetry.io/name')
        if service not in services:
            continue
        statuses = item.get('status', {}).get('containerStatuses', [])
        if not statuses or item.get('spec', {}).get('nodeName') not in ready:
            continue
        if not all(status.get('ready') is True and status.get('containerID') for status in statuses):
            continue
        pods.append({'namespace': item['metadata']['namespace'],
                     'name': item['metadata']['name'], 'uid': item['metadata']['uid'],
                     'service': service, 'node': item['spec']['nodeName'],
                     'pod_ip': item.get('status', {}).get('podIP'),
                     'container_ids': {status['name']: status['containerID'] for status in statuses},
                     'restart_counts': {status['name']: status['restartCount'] for status in statuses}})
    if not pods:
        raise ValueError('No Ready public Pod identities')
    pod_by_uid = {item['uid']: item for item in pods}
    for slice_item in kube['endpointslices']['items']:
        service = slice_item['metadata'].get('labels', {}).get('kubernetes.io/service-name')
        if service not in {pod['service'] for pod in pods}:
            continue
        for endpoint in slice_item.get('endpoints', []):
            target = endpoint.get('targetRef', {})
            pod = pod_by_uid.get(target.get('uid'))
            if pod and (pod['service'] != service or pod['name'] != target.get('name') or
                        pod['node'] != endpoint.get('nodeName')):
                raise ValueError('EndpointSlice contradicts public Pod identity')
    return {'schema_version': 1, 'public_only': True, 'case_id': case_id,
            'services': sorted({pod['service'] for pod in pods}), 'nodes': nodes,
            'pods': pods, 'service_calls': [], 'pod_communications': []}


def trace_rows(capture, known_services):
    window = capture['window']
    spans = {}
    for path in sorted((capture['path'] / 'traces').glob('*.json')):
        data = read_json(path)
        for trace in data.get('data', []):
            processes = trace.get('processes', {})
            for raw in trace.get('spans', []):
                stamp = int(raw['startTime']) * 1000
                if not window['start_unix_ns'] <= stamp < window['end_unix_ns']:
                    continue
                process = processes.get(raw.get('processID'), {})
                service = process.get('serviceName')
                if service not in known_services:
                    continue
                key = (trace['traceID'], raw['spanID'])
                tags = {item['key']: item.get('value') for item in raw.get('tags', [])
                        if item.get('key') in ('span.kind', 'rpc.service', 'net.peer.name',
                                               'otel.status_code', 'http.response.status_code')}
                parent = next((ref.get('spanID') for ref in raw.get('references', [])
                               if ref.get('refType') == 'CHILD_OF'), None)
                row = {'phase': capture['manifest']['phase'], 'time_unix_ns': stamp,
                       'trace_id': trace['traceID'], 'span_id': raw['spanID'],
                       'parent_span_id': parent, 'service': service,
                       'duration_ms': int(raw['duration']) / 1000,
                       'operation': raw.get('operationName', ''), 'tags': tags}
                if key in spans and spans[key] != row:
                    raise ValueError('Conflicting duplicate Jaeger span')
                spans[key] = row
    edges = defaultdict(list)
    for row in spans.values():
        parent = spans.get((row['trace_id'], row['parent_span_id']))
        if not parent or parent['service'] == row['service']:
            continue
        edge = parent['service'] + '->' + row['service']
        if parent['tags'].get('span.kind') == 'client':
            latency = parent['duration_ms']
        elif row['tags'].get('span.kind') == 'server':
            latency = row['duration_ms']
        else:
            continue
        edges[edge].append((row['time_unix_ns'], latency))
        row['parent_service'] = parent['service']
    return sorted(spans.values(), key=lambda row: row['time_unix_ns']), edges


def log_rows(capture, known_services):
    window = capture['window']
    rows, seen = [], set()
    for path in sorted((capture['path'] / 'logs').glob('page-*.json')):
        for hit in read_json(path).get('hits', {}).get('hits', []):
            key = (hit.get('_index'), hit.get('_id'))
            if key in seen:
                continue
            seen.add(key)
            source = hit.get('_source', {})
            service = source.get('resource', {}).get('service.name')
            if service not in known_services:
                continue
            stamp = datetime.fromisoformat((source.get('@timestamp') or
                                           source.get('observedTimestamp')).replace('Z', '+00:00'))
            stamp_ns = int(stamp.timestamp() * 1_000_000_000)
            if not window['start_unix_ns'] <= stamp_ns < window['end_unix_ns']:
                continue
            body = source.get('body', '')
            if not isinstance(body, str):
                body = json.dumps(body, ensure_ascii=True)
            severity = source.get('severity', {})
            level = severity.get('text', '') if isinstance(severity, dict) else str(severity)
            rows.append({'phase': capture['manifest']['phase'], 'time_unix_ns': stamp_ns,
                         'service': service, 'level': level, 'message': body[:1000]})
    return sorted(rows, key=lambda row: row['time_unix_ns'])


def metric_rows(capture, pods):
    window = capture['window']
    pod_by_name = {item['name']: item for item in pods}
    rows = []
    for query, expected in METRIC_NAMES.items():
        data = read_json(capture['path'] / 'metrics' / (query + '.json'))
        for series in data['data']['result']:
            labels = series['metric']
            if labels.get('__name__') != expected:
                raise ValueError('Unexpected Prometheus metric family: ' + query)
            pod_name = labels.get('pod')
            entity = None
            if pod_name:
                pod = pod_by_name.get(pod_name)
                if not pod:
                    continue
                container = labels.get('container')
                identity = pod['container_ids'].get(container)
                if not identity or labels.get('name') != identity.removeprefix('containerd://'):
                    raise ValueError('Prometheus metric/container identity mismatch: ' + pod_name)
                entity = f"POD/{pod['namespace']}/{pod['name']}"
            elif query.startswith('node_'):
                node = labels.get('k8s_node_name')
                if node:
                    entity = 'NODE/' + node
            if not entity:
                continue
            series_id = hashlib.sha256(json.dumps(labels, sort_keys=True).encode()).hexdigest()[:20]
            for stamp, raw in series.get('values', []):
                stamp_ns = int(float(stamp) * 1_000_000_000)
                if not window['start_unix_ns'] <= stamp_ns < window['end_unix_ns']:
                    continue
                value = float(raw)
                if not math.isfinite(value):
                    continue
                rows.append({'phase': capture['manifest']['phase'], 'time_unix_ns': stamp_ns,
                             'entity': entity, 'family': expected, 'value': value,
                             'series_id': series_id,
                             'cpu': labels.get('cpu') if query == 'node_cpu' else None})
    return sorted(rows, key=lambda row: (row['entity'], row['family'], row['series_id'],
                                          row['time_unix_ns']))


def check_pod_continuity(before, during):
    old = {pod['name']: pod for pod in before['pods']}
    new = {pod['name']: pod for pod in during['pods']}
    changed = []
    for name, pod in old.items():
        current = new.get(name)
        if current and (current['uid'] != pod['uid'] or
                        current['container_ids'] != pod['container_ids'] or
                        current['node'] != pod['node']):
            changed.append(name)
    if changed:
        raise ValueError('Pod UID or container identity changed: ' + ', '.join(changed))


def target_pod_coverage(result, target_name):
    if not target_name:
        return None
    before = next((pod for pod in result['metadata']['pods'] if pod['name'] == target_name), None)
    during = next((pod for pod in result['during_metadata']['pods']
                   if pod['name'] == target_name), None)
    if not before or not during or (before['uid'], before['node'], before['container_ids']) != (
            during['uid'], during['node'], during['container_ids']):
        raise ValueError('F02 target Pod UID/container/placement is not continuous')
    if len(before['container_ids']) != 1:
        raise ValueError('F02 target must have one unambiguous business container')
    container_id = next(iter(before['container_ids'].values()))
    entity = f"POD/{before['namespace']}/{before['name']}"
    sample_counts = {}
    series_ids = {}
    for phase, minimum in (('before', 20), ('during', 10)):
        sample_counts[phase] = {}
        series_ids[phase] = {}
        for family in POD_REQUIRED_METRICS:
            rows = [row for row in result['metrics'][phase]
                    if row['entity'] == entity and row['family'] == family]
            stamps = {row['time_unix_ns'] for row in rows}
            ids = {row['series_id'] for row in rows}
            if len(rows) != len(stamps) or len(ids) != 1 or len(rows) < minimum:
                raise ValueError('F02 target lacks stable ' + family + ' in ' + phase)
            sample_counts[phase][family] = len(rows)
            series_ids[phase][family] = next(iter(ids))
    if series_ids['before'] != series_ids['during']:
        raise ValueError('F02 target metric series identity changed between phases')
    return {'entity': entity, 'pod_uid': before['uid'], 'container_id': container_id,
            'node': before['node'], 'sample_counts': sample_counts,
            'series_ids': series_ids}


def safe_public(value):
    serialized = json.dumps(value, ensure_ascii=True)
    match = FORBIDDEN.search(serialized)
    if match:
        raise ValueError('Private token in public conversion: ' + match.group())
    return value


def build_base(captures, case_id, path_snapshot=None, frozen_candidates=None):
    validate_windows(captures)
    before_meta = kubernetes_metadata(captures['before'], case_id)
    during_meta = kubernetes_metadata(captures['during'], case_id)
    check_pod_continuity(before_meta, during_meta)
    known_services = set(before_meta['services'])
    traces = {}
    edges = {}
    logs = {}
    metrics = {}
    for phase in ('before', 'during'):
        traces[phase], edges[phase] = trace_rows(captures[phase], known_services)
        logs[phase] = log_rows(captures[phase], known_services)
        metrics[phase] = metric_rows(captures[phase], before_meta['pods'])
    calls = sorted(edges['before'])
    if not calls:
        raise ValueError('No cross-service calls observed in clean public traces')
    before_meta['service_calls'] = [{'source': edge.split('->', 1)[0],
                                     'destination': edge.split('->', 1)[1]} for edge in calls]
    if path_snapshot is not None:
        validate_path(path_snapshot)
    public_case_id = frozen_candidates['public_case_id'] if frozen_candidates else None
    candidates = build_candidates(before_meta, path_snapshot, public_case_id)
    if frozen_candidates:
        if frozen_candidates.get('case_id') != case_id or any(
                candidates.get(key) != frozen_candidates.get(key) for key in
                ('public_case_id', 'candidates', 'relations', 'candidate_counts')):
            raise ValueError('Formal public candidates or topology changed after pre-fault freeze')
    return {'metadata': before_meta, 'during_metadata': during_meta,
            'candidates': candidates, 'traces': traces, 'edges': edges,
            'logs': logs, 'metrics': metrics, 'calls': calls,
            'path_snapshot': path_snapshot}


def load_network_probe(path, captures, path_snapshot):
    if path_snapshot is None:
        raise ValueError('Network probe requires a verified pre-fault route map')
    entity = path_snapshot['network_path_entity']
    _, source_node, destination_node = entity.split('/', 2)
    destination_uids = {edge['destination_pod_uid'] for edge in
                        path_snapshot['service_edges'] if edge['relation'] == 'TRAVERSES'}
    destination_ips = {pod['pod_ip'] for pod in path_snapshot['pods']
                       if pod['uid'] in destination_uids}
    if len(destination_ips) != 1:
        raise ValueError('NetworkPath probe has no single verified destination Pod IP')
    destination_ip = next(iter(destination_ips))
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len(rows) < 60 or [row['sample_index'] for row in rows] != list(range(len(rows))):
        raise ValueError('Network probe has missing or duplicate 15-second samples')
    phases = {'before': [], 'during': []}
    for row in rows:
        if (row.get('schema_version') != 1 or row.get('source') != 'live_node_path_probe' or
                row.get('source_node') != source_node or
                row.get('destination_node') != destination_node or
                row.get('destination_ip') != destination_ip or
                not 0 <= row['received'] <= row['sent'] or
                len(row['rtt_ms']) != row['received'] or row['sent'] < 5 or
                abs(row['observed_unix_ns'] - row['scheduled_unix_ns']) > 2_000_000_000 or
                any(not isinstance(value, (int, float)) or value < 0 or
                    not math.isfinite(value) for value in row['rtt_ms'])):
            raise ValueError('Invalid public network probe sample')
        matches = [phase for phase in phases if
                   captures[phase]['window']['start_unix_ns'] <= row['scheduled_unix_ns'] <
                   captures[phase]['window']['end_unix_ns']]
        if len(matches) != 1:
            raise ValueError('Network probe sample falls outside one public phase')
        phases[matches[0]].append(row)
    if len(phases['before']) < 40 or len(phases['during']) < 20:
        raise ValueError('Network probe does not cover both observation windows')
    return {'path': entity, 'phases': phases, 'sha256': digest(path)}


def time_bins(window):
    start = window['start_unix_ns'] // BIN_NS * BIN_NS
    return list(range(start, window['end_unix_ns'], BIN_NS))


def resource_series(result, captures):
    bins = {phase: time_bins(captures[phase]['window']) for phase in ('before', 'during')}
    by_entity = defaultdict(lambda: defaultdict(lambda: {'before': {}, 'during': {}}))
    pods = {f"POD/{pod['namespace']}/{pod['name']}": pod for pod in result['metadata']['pods']}
    for phase in ('before', 'during'):
        series = defaultdict(list)
        for row in result['metrics'][phase]:
            series[(row['entity'], row['family'], row['series_id'])].append(row)
        values = defaultdict(lambda: defaultdict(list))
        for (entity, family, _), rows in series.items():
            rows.sort(key=lambda row: row['time_unix_ns'])
            prior = None
            for row in rows:
                value = row['value']
                if family in {METRIC_NAMES[name] for name in COUNTERS}:
                    if prior is None or value < prior['value']:
                        prior = row
                        continue
                    seconds = (row['time_unix_ns'] - prior['time_unix_ns']) / 1e9
                    if seconds <= 0:
                        raise ValueError('Duplicate Prometheus sample timestamp')
                    value = (value - prior['value']) / seconds
                prior = row
                stamp = row['time_unix_ns'] // BIN_NS * BIN_NS
                values[(entity, family, stamp)]['samples'].append(value)
        for (entity, family, stamp), data in values.items():
            value = median(data['samples']) if family == METRIC_NAMES['node_cpu'] else sum(data['samples'])
            by_entity[entity][family][phase][stamp] = value
            pod = pods.get(entity)
            if pod:
                service = 'SERVICE/' + pod['service']
                node = 'NODE/' + pod['node']
                for parent in (service, node):
                    family_name = 'pod_aggregate_' + family
                    old = by_entity[parent][family_name][phase].get(stamp, 0.0)
                    by_entity[parent][family_name][phase][stamp] = old + value
    output = {}
    for entity, families in by_entity.items():
        output[entity] = {family: {phase: [phases[phase].get(stamp) for stamp in bins[phase]]
                                   for phase in ('before', 'during')}
                          for family, phases in families.items()}
    probe = result.get('network_probe')
    if probe:
        path = probe['path']
        output[path] = {}
        for family, extract in (
                ('network_path_rtt_ms', lambda row: median(row['rtt_ms']) if row['rtt_ms'] else None),
                ('network_path_loss_fraction', lambda row: (row['sent'] - row['received']) /
                 row['sent'])):
            output[path][family] = {}
            for phase in ('before', 'during'):
                values = {row['scheduled_unix_ns'] // BIN_NS * BIN_NS: extract(row)
                          for row in probe['phases'][phase]}
                output[path][family][phase] = [values.get(stamp) for stamp in bins[phase]]
                missing = sum(value is None for value in output[path][family][phase])
                if missing / len(bins[phase]) > 0.25:
                    raise ValueError('Network probe exceeds missing-series budget: ' + family)
    return output, bins


def invocation_series(result, bins):
    output = {}
    for edge in result['calls']:
        phases = {}
        for phase in ('before', 'during'):
            grouped = defaultdict(list)
            for stamp, latency in result['edges'][phase].get(edge, []):
                grouped[stamp // BIN_NS * BIN_NS].append(latency)
            phases[phase] = [percentile(grouped[stamp], 0.95) if grouped[stamp] else None
                             for stamp in bins[phase]]
        output[edge] = phases
    return output


def metric_abnormal(reference, observed, null_window=False):
    if not null_window:
        return resource_metric(reference, observed)
    if len(reference) < 20 or len(observed) < 4:
        return None
    baseline = median(reference)
    current = median(observed)
    mad = median(abs(value - baseline) for value in reference)
    return abs(current - baseline) > max(3 * 1.4826 * mad,
                                         0.5 * max(abs(baseline), 1e-9))


def candidate_signals(entity, result, resources, captures, minute=None):
    observed = {'metrics': [], 'logs': [], 'traces': [], 'network': []}
    before_bins = time_bins(captures['before']['window'])
    minute_start = (captures['before']['window']['start_unix_ns'] +
                    minute * 60_000_000_000) if minute is not None else None
    minute_end = minute_start + 60_000_000_000 if minute_start is not None else None
    for family, phases in resources.get(entity, {}).items():
        before = [(stamp, value) for stamp, value in zip(before_bins, phases['before'])
                  if value is not None]
        if minute is None:
            reference = [value for _, value in before]
            during = [value for value in phases['during'] if value is not None]
        else:
            reference = [value for stamp, value in before
                         if not minute_start <= stamp < minute_end]
            during = [value for stamp, value in before
                      if minute_start <= stamp < minute_end]
        if entity.startswith('NETWORK_PATH/') and family == 'network_path_loss_fraction':
            probe_rows = result['network_probe']['phases']
            clean = probe_rows['before']
            if minute is None:
                baseline, compared = clean, probe_rows['during']
            else:
                baseline = [row for row in clean
                            if not minute_start <= row['scheduled_unix_ns'] < minute_end]
                compared = [row for row in clean
                            if minute_start <= row['scheduled_unix_ns'] < minute_end]
            before_sent = sum(row['sent'] for row in baseline)
            during_sent = sum(row['sent'] for row in compared)
            before_lost = sum(row['sent'] - row['received'] for row in baseline)
            during_lost = sum(row['sent'] - row['received'] for row in compared)
            if minute is None:
                abnormal = network_drop_fraction(before_lost, before_sent,
                                                 during_lost, during_sent)
            elif before_sent >= 100 and during_sent >= 20:
                abnormal = during_lost / during_sent > before_lost / before_sent + 0.01
            else:
                abnormal = None
        else:
            abnormal = metric_abnormal(reference, during, null_window=minute is not None)
        modality = 'network' if entity.startswith('NETWORK_PATH/') else 'metrics'
        observed[modality].append({'family': modality + '.' + family,
                                   'observed': abnormal is not None,
                                   'abnormal': abnormal, 'before_samples': len(reference),
                                   'during_samples': len(during)})
    if entity.startswith('SERVICE/'):
        service = entity.removeprefix('SERVICE/')
        errors = {}
        has_logs = {}
        for phase in ('before', 'during'):
            rows = [row for row in result['logs'][phase] if row['service'] == service]
            if minute is not None:
                phase_rows = result['logs']['before']
                if phase == 'before':
                    rows = [row for row in phase_rows if row['service'] == service and
                            not minute_start <= row['time_unix_ns'] < minute_end]
                else:
                    rows = [row for row in phase_rows if row['service'] == service and
                            minute_start <= row['time_unix_ns'] < minute_end]
            has_logs[phase] = bool(rows)
            errors[phase] = sum(row['level'].upper() in ('ERROR', 'FATAL', 'WARN')
                                for row in rows)
        before_seconds = ((captures['before']['window']['end_unix_ns'] -
                           captures['before']['window']['start_unix_ns']) / 1e9 - 60)
        if minute is None:
            before_seconds = (
                captures['before']['window']['end_unix_ns'] -
                captures['before']['window']['start_unix_ns']) / 1e9
        during_seconds = 60 if minute is not None else (
            captures['during']['window']['end_unix_ns'] -
            captures['during']['window']['start_unix_ns']) / 1e9
        abnormal = error_log_rate(errors['before'], errors['during'],
                                  before_seconds, during_seconds) if all(has_logs.values()) else None
        observed['logs'].append({'family': 'log.error_rate', 'observed': abnormal is not None,
                                 'abnormal': abnormal, 'before_errors': errors['before'],
                                 'during_errors': errors['during']})
    if entity.startswith('SERVICE/'):
        trace_edges = [edge for edge in result['calls']
                       if entity.removeprefix('SERVICE/') in edge.split('->')]
    elif entity.startswith('NETWORK_PATH/') and result.get('path_snapshot'):
        trace_edges = [row['source_service'] + '->' + row['destination_service']
                       for row in result['path_snapshot']['service_edges']
                       if row['relation'] == 'TRAVERSES' and row['network_path_entity'] == entity]
    else:
        trace_edges = []
    for edge in trace_edges:
        if minute is None:
            reference = [latency for _, latency in result['edges']['before'].get(edge, [])]
            during = [latency for _, latency in result['edges']['during'].get(edge, [])]
            abnormal = latency_p95(reference, during)
        else:
            reference = [latency for stamp, latency in result['edges']['before'].get(edge, [])
                         if not minute_start <= stamp < minute_end]
            during = [latency for stamp, latency in result['edges']['before'].get(edge, [])
                      if minute_start <= stamp < minute_end]
            abnormal = None if len(reference) < 30 or len(during) < 6 else (
                percentile(during, 0.95) > max(1.5 * percentile(reference, 0.95),
                                               percentile(reference, 0.95) + 10))
        observed['traces'].append({'family': 'trace.' + edge, 'observed': abnormal is not None,
                                   'abnormal': abnormal, 'before_samples': len(reference),
                                   'during_samples': len(during)})
    return observed


def simple_null(result, resources, captures, candidates):
    before = captures['before']['window']
    minutes = (before['end_unix_ns'] - before['start_unix_ns']) // 60_000_000_000
    if minutes < 10:
        raise ValueError('Clean window has fewer than ten 60-second null slices')
    maxima = {layer: [] for layer in ('SERVICE', 'POD', 'NODE', 'NETWORK_PATH')}
    for minute in range(10):
        by_layer = defaultdict(list)
        for item in candidates:
            entity, layer = item['entity'], item['layer']
            count, score, _ = score_signals(candidate_signals(entity, result, resources,
                                                                captures, minute))
            if count >= 4 and score is not None:
                by_layer[layer].append(score)
        for layer in maxima:
            maxima[layer].append(max(by_layer[layer], default=0.0))
    return maxima


def same_calibration(current, frozen):
    if not isinstance(frozen, dict) or set(current) != set(frozen):
        return False
    for kind, layers in current.items():
        if not isinstance(frozen[kind], dict) or set(layers) != set(frozen[kind]):
            return False
        for layer, values in layers.items():
            archived = frozen[kind][layer]
            if (not isinstance(archived, list) or len(values) != len(archived) or
                    any(not isinstance(value, (int, float)) or
                        not isinstance(expected, (int, float)) or
                        not math.isclose(value, expected, rel_tol=1e-9, abs_tol=1e-12)
                        for value, expected in zip(values, archived))):
                return False
    return True


def microrca_null(invocation, resources, bins, candidates, relations):
    maxima = {layer: [] for layer in ('SERVICE', 'POD', 'NODE', 'NETWORK_PATH')
              if any(item['layer'] == layer for item in candidates)}
    for minute in range(10):
        selected = bins['before'][minute * 4:(minute + 1) * 4]
        if len(selected) != 4:
            raise ValueError('Clean null minute lacks four 15-second bins')
        selected_set = set(selected)
        before_indices = [index for index, stamp in enumerate(bins['before'])
                          if stamp not in selected_set]
        during_indices = [index for index, stamp in enumerate(bins['before'])
                          if stamp in selected_set]
        before_times = [bins['before'][index] for index in before_indices]
        during_times = [bins['before'][index] for index in during_indices]
        pseudo = {'schema_version': 1, 'public_only': True,
                  'public_case_id': '00000000-0000-4000-8000-000000000000',
                  'variant': 'B', 'candidates': candidates, 'relations': relations,
                  'time_bins_unix_ns': before_times + during_times,
                  'invocation_series': {}, 'resource_series': {},
                  'clean_null_pagerank_maxima': {layer: [0.0] * 8 for layer in maxima}}
        for edge, phases in invocation.items():
            values = phases['before']
            pseudo['invocation_series'][edge] = {
                'before': [values[index] for index in before_indices],
                'during': [values[index] for index in during_indices]}
        for entity, families in resources.items():
            pseudo['resource_series'][entity] = {}
            for family, phases in families.items():
                values = phases['before']
                pseudo['resource_series'][entity][family] = (
                    [values[index] for index in before_indices] +
                    [values[index] for index in during_indices])
        prediction = run_microrca(pseudo)
        pagerank = prediction['pagerank_scores']
        for layer in maxima:
            maxima[layer].append(max(pagerank.get(item['entity'], 0.0) for item in candidates
                                     if item['layer'] == layer))
    return maxima


def public_observations(result, resources, candidates, variant):
    entities = {item['entity'] for item in candidates}
    observations = {'metrics': {}, 'logs': {}, 'traces': {}, 'network': {}}
    for entity in entities:
        observations['metrics'][entity] = [row for phase in ('before', 'during')
                                           for row in result['metrics'][phase]
                                           if row['entity'] == entity]
        if entity.startswith('SERVICE/'):
            name = entity.removeprefix('SERVICE/')
            observations['logs'][entity] = [row for phase in ('before', 'during')
                                            for row in result['logs'][phase]
                                            if row['service'] == name]
            observations['traces'][entity] = [row for phase in ('before', 'during')
                                              for row in result['traces'][phase]
                                              if row['service'] == name]
    if variant == 'B':
        for entity in entities:
            if entity.startswith('NETWORK_PATH/'):
                probe = result.get('network_probe')
                observations['network'][entity] = ([
                    {'phase': phase, 'time_unix_ns': row['scheduled_unix_ns'],
                     'rtt_ms': row['rtt_ms'], 'sent': row['sent'],
                     'received': row['received']}
                    for phase in ('before', 'during') for row in probe['phases'][phase]]
                    if probe and probe['path'] == entity else [])
    return observations


def convert(captures, case_id, output, path_snapshot=None, network_probe_path=None,
            target_pod_name=None, preflight_candidates=None, preflight_calibration=None):
    if output.exists():
        raise ValueError('Refusing to overwrite a live conversion')
    if (preflight_candidates is None) != (preflight_calibration is None):
        raise ValueError('Formal candidate and calibration freezes must be supplied together')
    frozen_candidates = read_json(preflight_candidates) if preflight_candidates else None
    frozen_calibration = read_json(preflight_calibration) if preflight_calibration else None
    result = build_base(captures, case_id, path_snapshot, frozen_candidates)
    pod_coverage = target_pod_coverage(result, target_pod_name)
    if network_probe_path:
        result['network_probe'] = load_network_probe(network_probe_path, captures, path_snapshot)
    resources, bins = resource_series(result, captures)
    invocation = invocation_series(result, bins)
    candidate_record = result['candidates']
    uuid_value = candidate_record['public_case_id']
    all_candidates = candidate_record['candidates']
    all_relations = candidate_record['relations']
    null_simple = simple_null(result, resources, captures, all_candidates)
    calibrations = {}
    for variant in ('A', 'B'):
        candidates = [item for item in all_candidates if variant == 'B' or
                      item['layer'] != 'NETWORK_PATH']
        relations = [item for item in all_relations if variant == 'B' or
                     item['type'] != 'TRAVERSES']
        calibration = {
            'clean_null_window_maxima': {layer: values for layer, values in null_simple.items()
                                         if variant == 'B' or layer != 'NETWORK_PATH'},
            'clean_null_pagerank_maxima': microrca_null(invocation, resources, bins,
                                                       candidates, relations)}
        if frozen_calibration:
            archived = frozen_calibration.get('variants', {}).get(variant)
            if (frozen_calibration.get('case_id') != case_id or
                    frozen_calibration.get('public_only') is not True or
                    not same_calibration(calibration, archived)):
                raise ValueError('Clean-null calibration changed after pre-fault freeze: ' + variant)
            calibration = archived
        calibrations[variant] = calibration
    output.mkdir(parents=True)
    canonical = output / 'canonical'
    for modality in ('metrics', 'logs', 'traces'):
        write_jsonl(canonical / (modality + '.jsonl'),
                    [row for phase in ('before', 'during') for row in result[modality][phase]])
    write_json(canonical / 'windows.json', {phase: capture['window']
                                           for phase, capture in captures.items()})
    write_json(canonical / 'kubernetes.json', result['metadata'])
    write_json(canonical / 'service-topology.json', result['metadata']['service_calls'])
    write_json(canonical / 'node-placement.json', result['metadata']['pods'])
    if pod_coverage:
        write_json(canonical / 'target-pod-coverage.json', pod_coverage)
    write_json(output / 'candidate-universe.json', candidate_record)
    summaries = {}
    for variant in ('A', 'B'):
        candidates = [item for item in all_candidates if variant == 'B' or
                      item['layer'] != 'NETWORK_PATH']
        relations = [item for item in all_relations if variant == 'B' or
                     item['type'] != 'TRAVERSES']
        signals = {item['entity']: candidate_signals(item['entity'], result, resources,
                                                     captures) for item in candidates}
        calibration = calibrations[variant]
        public_entities = {item['entity'] for item in candidates}
        public_metadata = {key: value for key, value in result['metadata'].items()
                           if key != 'case_id'}
        bundle = {'schema_version': 1, 'public_only': True,
                  'public_case_id': uuid_value, 'variant': variant,
                  'candidates': candidates, 'relations': relations,
                  'windows': {phase: captures[phase]['window'] for phase in ('before', 'during')},
                  'time_bins_unix_ns': bins['before'] + bins['during'],
                  'invocation_series': invocation,
                  'resource_series': {entity: {family: phases['before'] + phases['during']
                                               for family, phases in families.items()}
                                      for entity, families in resources.items()
                                      if entity in public_entities},
                  'clean_null_window_maxima': calibration['clean_null_window_maxima'],
                  'clean_null_pagerank_maxima': calibration['clean_null_pagerank_maxima'],
                  'signals': signals,
                  'observations': public_observations(result, resources, candidates, variant)}
        if variant == 'B' and path_snapshot:
            bundle['network_context'] = path_snapshot
        safe_public(bundle)
        public = output / 'public' / ('variant-' + variant.lower())
        write_json(public / 'bundle.json', bundle)
        write_json(public / 'service-topology.json', result['metadata']['service_calls'])
        write_json(public / 'pod-placement.json', result['metadata']['pods'])
        write_json(public / 'node-placement.json', result['metadata']['nodes'])
        write_json(public / 'metrics/observations.json', bundle['observations']['metrics'])
        write_json(public / 'logs/observations.json', bundle['observations']['logs'])
        write_json(public / 'traces/observations.json', bundle['observations']['traces'])
        write_json(public / 'kubernetes/metadata.json', public_metadata)
        if variant == 'B':
            write_json(public / 'network-paths.json', path_snapshot or {})
            path_result = validate_path(path_snapshot) if path_snapshot else {}
            write_json(public / 'edge-path-map.json', {
                'traversing_edges': path_result.get('traversing_edges', []),
                'control_edges': path_result.get('control_edges', []),
                'route_evidence': (path_snapshot or {}).get('route_evidence', []),
            })
            write_json(public / 'normal-network-observations.json',
                       {'pre_fault_route_baseline': (path_snapshot or {}).get(
                           'normal_observations', {}),
                        'window_observations': bundle['observations']['network']})
        write_json(public / 'case-quality.json', {
            'before_seconds': (captures['before']['window']['end_unix_ns'] -
                               captures['before']['window']['start_unix_ns']) / 1e9,
            'during_seconds': (captures['during']['window']['end_unix_ns'] -
                               captures['during']['window']['start_unix_ns']) / 1e9,
            'before_during_comparable': True, 'observability_pass': False,
            'public_private_leak_scan_pass': True})
        summaries[variant] = {'candidates': len(candidates), 'signals': len(signals)}
    for public_file in (output / 'public').rglob('*'):
        if public_file.is_file():
            match = FORBIDDEN.search(public_file.read_text())
            if match:
                raise ValueError('Private token in public file ' + str(public_file))
    files = [file for file in output.rglob('*') if file.is_file()]
    write_json(output / 'conversion-manifest.json', {
        'status': 'STAGE4F_LIVE_CONVERSION_COMPLETE',
        'trace_source': TRACE_SOURCE,
        'public_case_id': uuid_value,
        'input_capture_sha256': {phase: digest(capture['path'] / 'capture-manifest.json')
                                 for phase, capture in captures.items()},
        'network_proof_sha256': hashlib.sha256(json.dumps(path_snapshot, sort_keys=True).encode()).hexdigest()
                                if path_snapshot else None,
        'network_probe_sha256': digest(network_probe_path) if network_probe_path else None,
        'preflight_candidates_sha256': (digest(preflight_candidates)
                                        if preflight_candidates else None),
        'preflight_calibration_sha256': (digest(preflight_calibration)
                                         if preflight_calibration else None),
        'output_sha256': {str(file.relative_to(output)): digest(file) for file in sorted(files)},
    })
    return {'status': 'STAGE4F_LIVE_CONVERSION_COMPLETE',
            'public_case_id': uuid_value, 'variants': summaries,
            'clean_edges': len(result['calls'])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--during', type=Path, required=True)
    parser.add_argument('--after', type=Path)
    parser.add_argument('--network-proof', type=Path)
    parser.add_argument('--network-probe', type=Path)
    parser.add_argument('--target-pod')
    parser.add_argument('--preflight-candidates', type=Path)
    parser.add_argument('--preflight-calibration', type=Path)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    captures = {'before': load_capture(args.before, 'before'),
                'during': load_capture(args.during, 'during')}
    if args.after:
        captures['after'] = load_capture(args.after, 'after')
    proof = read_json(args.network_proof) if args.network_proof else None
    print(json.dumps(convert(captures, args.case_id, args.output, proof,
                             args.network_probe, args.target_pod,
                             args.preflight_candidates, args.preflight_calibration)))


if __name__ == '__main__':
    main()
