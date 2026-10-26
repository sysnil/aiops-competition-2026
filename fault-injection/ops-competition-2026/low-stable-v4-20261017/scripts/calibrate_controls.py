"""Measure clean LOW-STABLE control noise in frozen no-fault windows."""

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlparse


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026/low-stable-v4-20261017')
sys.path.insert(0, str(OPS / 'sf011-real-adapter-v1-20261017/scripts'))
from pilot_exchange_schema import span_attributes

RUNS = (DATA / 'validation-runs-v5/VAL5', DATA / 'validation-runs-v6/VAL6')
SLICES = {'before': (300, 600), 'during': (600, 630), 'after': (720, 900)}
CONTROL_AGENT = {'VAL5': 'oc-business-workflow/1.0',
                 'VAL6': 'business-control/1.0'}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def p95(values):
    values = sorted(values)
    return values[math.ceil(.95 * len(values)) - 1] if values else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Clean calibration already exists')
    results = {}
    for run in RUNS:
        assessment = json.loads((run / 'assessment.json').read_text())
        schedule = json.loads((run / 'schedule.json').read_text())
        if (assessment['status'] != 'NO_FAULT_VALIDATION_PASS' or
                assessment['faults_injected'] != 0 or
                any(assessment['missing_proxy_trace_ids'].values())):
            raise RuntimeError(f'Unqualified clean window: {run.name}')
        start = stamp(schedule['start_utc'])
        controls = defaultdict(lambda: defaultdict(dict))
        recommendation = defaultdict(set)
        frontend_callers = defaultdict(set)
        for file in sorted((run / 'capture').glob(
                'chunk-*/trace-final/during/trace-storage-pages/page-*.json')):
            for hit in json.loads(file.read_text())['hits']['hits']:
                span = hit['_source']
                at = span['startTime'] / 1_000_000
                periods = [name for name, (left, right) in SLICES.items()
                           if start + left <= at < start + right]
                if not periods:
                    continue
                service = span['process']['serviceName']
                attrs = span_attributes(span)
                if service == 'frontend-proxy' and attrs.get('span.kind') == 'server':
                    agent = attrs.get('user_agent')
                    path = urlparse(attrs.get('http.url') or '').path
                    group = ('image' if agent == CONTROL_AGENT[run.name] and
                             path.startswith('/images/products/') else
                             'cart' if agent == CONTROL_AGENT[run.name] and
                             path == '/api/cart' else None)
                    if group:
                        for period in periods:
                            old = controls[group][period].get(span['traceID'], 0)
                            controls[group][period][span['traceID']] = max(
                                span['duration'] / 1000, old)
                if service == 'recommendation' and attrs.get('span.kind') == 'server':
                    for period in periods:
                        recommendation[period].add(span['traceID'])
                if (service == 'frontend' and attrs.get('span.kind') == 'client' and
                        attrs.get('net.peer.name') == 'recommendation' and
                        attrs.get('rpc.method') == 'ListRecommendations'):
                    for period in periods:
                        frontend_callers[period].add(span['traceID'])
        grouped = {}
        for group in ('image', 'cart'):
            grouped[group] = {period: {'trace_ids': len(controls[group][period]),
                                       'p95_ms': p95(controls[group][period].values())}
                              for period in SLICES}
            before = grouped[group]['before']['p95_ms']
            during = grouped[group]['during']['p95_ms']
            after = grouped[group]['after']['p95_ms']
            if not all(value is not None and value > 0 for value in (before, during, after)):
                raise RuntimeError('Clean control slice has no valid latency')
            grouped[group]['during_to_before_p95_ratio'] = during / before
            grouped[group]['after_to_before_p95_ratio'] = after / before
            grouped[group]['during_minus_before_p95_ms'] = during - before
        before_servers = len(recommendation['before'])
        during_servers = len(recommendation['during'])
        if before_servers < 30 or len(frontend_callers['during']) < 30:
            raise RuntimeError('Clean Recommendation flow lacks sufficient samples')
        results[run.name] = {
            'assessment_sha256': sha(run / 'assessment.json'),
            'schedule_sha256': sha(run / 'schedule.json'),
            'control_agent': CONTROL_AGENT[run.name],
            'controls': grouped,
            'recommendation_server_traces': {name: len(recommendation[name])
                                             for name in SLICES},
            'recommendation_caller_traces': {name: len(frontend_callers[name])
                                             for name in SLICES},
            'normalized_30s_recommendation_completion_ratio':
                during_servers / (before_servers * 30 / 300),
        }
    result = {'status': 'LOW_STABLE_INDEPENDENT_NOFAULT_CONTROL_AUDIT',
              'slices_seconds': SLICES, 'runs': results,
              'faults_injected': 0, 'formal_results_used': False,
              'admission_contract_modified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
