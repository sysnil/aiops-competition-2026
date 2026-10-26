#!/usr/bin/env python3
"""Join real Checkout client and Payment server Spans by Trace identity."""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

import pandas as pd
import plotly.express as px


SOURCE = Path('/root/cross-layer-workload-generation/scripts/trace-coverage.py')
spec = importlib.util.spec_from_file_location('phase2_trace_reader', SOURCE)
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


def resource_tags(span):
    return {row.get('key'): row.get('value') for row in span.get('process', {}).get('tags', [])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('Refusing overwrite')
    args.output.mkdir(parents=True)
    schedule = json.loads((args.run / 'load_pair/schedule.json').read_text())
    start = datetime.fromisoformat(schedule['scheduled_start_utc'])
    end = start + timedelta(seconds=300)
    pods = json.loads((args.run / 'capture-pipeline/captures/before/kubernetes/pods.json').read_text())['items']
    uid_service = {p['metadata']['uid']: p['metadata']['labels'].get('opentelemetry.io/name')
                   for p in pods if not p['metadata'].get('deletionTimestamp')}
    issued = set()
    for line in (args.run / 'load_pair/state-machine/sessions.jsonl').read_text().splitlines():
        for step in json.loads(line)['steps']:
            if step['operation'] == 'checkout' and step.get('trace_id'):
                issued.add(step['trace_id'])
    query = {'bool': {'filter': [
        {'range': {'startTimeMillis': {'gte': int(start.timestamp() * 1000),
                                      'lt': int((end + timedelta(seconds=30)).timestamp() * 1000)}}},
        {'terms': {'traceID': sorted(issued)}},
        {'terms': {'process.serviceName': ['checkout', 'payment']}}]}}
    spans, total = reader.scan(query)
    by_trace = defaultdict(dict)
    for span in spans:
        by_trace[span['traceID']][span['spanID']] = span
    payment_bins = defaultdict(Counter)
    checkout_servers = Counter()
    source_to_target = Counter()
    known_source = 0
    unknown_source = 0
    uid_errors = []
    examples = []
    for span in spans:
        service = span.get('process', {}).get('serviceName')
        uid = resource_tags(span).get('k8s.pod.uid')
        kind = (span.get('tag') or {}).get('span@kind')
        if service == 'checkout' and kind == 'server':
            if uid_service.get(uid) == 'checkout':
                checkout_servers[uid] += 1
            else:
                uid_errors.append({'service': service, 'trace_id': span['traceID'], 'uid': uid})
        if service != 'payment' or kind != 'server':
            continue
        minute = int((span['startTimeMillis'] / 1000 - start.timestamp()) // 60)
        if not 0 <= minute < 5:
            continue
        if uid_service.get(uid) != 'payment':
            uid_errors.append({'service': service, 'trace_id': span['traceID'], 'uid': uid})
            continue
        payment_bins[uid][minute] += 1
        source_uid = None
        for ref in span.get('references') or []:
            parent = by_trace[span['traceID']].get(ref.get('spanID'))
            if parent and parent.get('process', {}).get('serviceName') == 'checkout':
                candidate = resource_tags(parent).get('k8s.pod.uid')
                if uid_service.get(candidate) == 'checkout':
                    source_uid = candidate
                    break
        if source_uid:
            known_source += 1
            source_to_target[(source_uid, uid)] += 1
        else:
            unknown_source += 1
        if len(examples) < 8:
            examples.append({'trace_id': span['traceID'], 'payment_span_id': span['spanID'],
                             'payment_pod_uid': uid, 'checkout_pod_uid':
                             source_uid or 'UNATTRIBUTED', 'minute': minute,
                             'operation': span.get('operationName')})
    payment_uids = sorted(uid for uid, service in uid_service.items() if service == 'payment')
    rows = [{'payment_pod_uid': uid, 'minute': minute, 'server_spans': payment_bins[uid][minute]}
            for uid in payment_uids for minute in range(5)]
    result = {'run_id': schedule['run_id'], 'source': 'generated checkout Trace IDs joined to raw Jaeger Checkout client and Payment server Spans',
              'issued_checkout_trace_ids': len(issued), 'queried_spans': total,
              'checkout_server_spans_by_uid': dict(checkout_servers),
              'payment_server_spans_by_uid': {uid: sum(payment_bins[uid].values()) for uid in payment_uids},
              'payment_per_minute': rows,
              'checkout_to_payment_spans': [{'checkout_pod_uid': a, 'payment_pod_uid': b,
                                             'calls': count} for (a, b), count in sorted(source_to_target.items())],
              'known_checkout_source_spans': known_source,
              'unknown_checkout_source_spans': unknown_source,
              'uid_mismatch_or_missing': uid_errors[:20],
              'uid_error_count': len(uid_errors), 'examples': examples,
              'no_direct_payment_pod_requests': True}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    fig = px.bar(pd.DataFrame(rows), x='minute', y='server_spans', color='payment_pod_uid',
                 barmode='group', title='Real Payment business calls by Pod UID')
    fig.write_html(args.output / 'payment-pod-calls.html', include_plotlyjs=True)
    print(json.dumps({'run_id': result['run_id'], 'payment_by_uid':
                      result['payment_server_spans_by_uid'], 'checkout_by_uid':
                      result['checkout_server_spans_by_uid'], 'unknown_source': unknown_source,
                      'uid_errors': len(uid_errors)}))


if __name__ == '__main__':
    main()
