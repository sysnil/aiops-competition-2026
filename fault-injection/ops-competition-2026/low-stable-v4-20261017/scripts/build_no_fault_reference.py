"""Summarize immutable LOW-STABLE no-fault windows for draft annotations."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017')
sys.path.insert(0, str(OPS / 'sf011-real-adapter-v1-20261017/scripts'))
from pilot_exchange_schema import canonical, span_attributes

RUNS = (DATA / 'validation-runs-v5/VAL5', DATA / 'validation-runs-v6/VAL6')
CONFIG_SHA = 'ad42e84b2a1d25b6b9b716f2a119f8d3ab6c50a975b860f3a8eed939618dd05f'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('No-fault reference already exists')
    memory_values = defaultdict(list)
    trace_values = defaultdict(list)
    provenance = {}
    for run in RUNS:
        assessment = read(run / 'assessment.json')
        schedule = read(run / 'schedule.json')
        if (assessment['status'] != 'NO_FAULT_VALIDATION_PASS' or
                assessment['faults_injected'] != 0 or
                assessment['config_sha256'] != CONFIG_SHA or
                schedule['config_sha256'] != CONFIG_SHA or
                any(assessment['missing_proxy_trace_ids'].values()) or
                assessment['request_counts']['image_asset'] != 1800 or
                assessment['request_counts']['cart_view'] != 1800 or
                assessment['request_counts']['home'] != 1800 or
                assessment['request_counts']['recommendations'] != 1800):
            raise RuntimeError(f'No-fault reference not comparable: {run.name}')
        provenance[run.name] = {
            'assessment_sha256': sha(run / 'assessment.json'),
            'schedule_sha256': sha(run / 'schedule.json'),
            'seed': schedule['seed'],
            'faults_injected': 0,
        }
        for file in sorted((run / 'capture').glob('chunk-*/captures/during/metrics/memory.json')):
            for series in read(file)['data']['result']:
                metric = series['metric']
                key = canonical([metric.get('namespace'), metric.get('pod'),
                                 metric.get('id')])
                memory_values[key].extend(float(value) for _, value in series['values'])
        for file in sorted((run / 'capture').glob(
                'chunk-*/trace-final/during/trace-storage-pages/page-*.json')):
            for hit in read(file)['hits']['hits']:
                span = hit['_source']
                attrs = span_attributes(span)
                key = canonical([span['process']['serviceName'],
                                 span['operationName'], attrs.get('span.kind', 'UNKNOWN')])
                trace_values[key].append(span['duration'] / 1000)
    if not memory_values or not trace_values:
        raise RuntimeError('No-fault Metrics or Traces missing')
    memory = {}
    for key, values in memory_values.items():
        median = statistics.median(values)
        memory[key] = {'samples': len(values), 'median_bytes': median,
                       'mad_bytes': statistics.median(abs(value - median)
                                                      for value in values)}
    traces = {key: {'samples': len(values),
                    'p95_ms': sorted(values)[int((len(values) - 1) * .95)]}
              for key, values in trace_values.items() if len(values) >= 10}
    result = {
        'status': 'OBSERVED_NO_FAULT_REFERENCE',
        'at_utc': datetime.now(timezone.utc).isoformat(),
        'memory': memory, 'traces': traces, 'provenance': provenance,
        'comparability': {
            'collector_config_sha256': CONFIG_SHA,
            'rate_sessions_per_second': 0.7,
            'controls_rates': {'image': 1, 'cart': 1, 'home': 1, 'recommendations': 1},
            'limits': ['different seeds and time of day',
                       'different process uptime/cache state',
                       'memory reference joined only on exact public Pod/cgroup identity',
                       'not an independent calibrated null distribution'],
        },
        'injection_plan_or_gt_read': False,
        'admission_thresholds_changed': False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'memory_series': len(memory),
                      'trace_operation_groups': len(traces),
                      'reference_sha256': sha(args.output)}))


if __name__ == '__main__':
    main()
