#!/usr/bin/env python3
"""Count real checkout->cart calls from generated business Trace IDs."""

import argparse
from collections import defaultdict
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


def quantile(values, ratio):
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * ratio)] if ordered else None


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
    generated = set()
    checkout_results = []
    for line in (args.run / 'load_pair/state-machine/sessions.jsonl').read_text().splitlines():
        session = json.loads(line)
        for step in session['steps']:
            if step.get('trace_id'):
                generated.add(step['trace_id'])
            if step['operation'] == 'checkout':
                checkout_results.append(step)
    for stream in ('image_asset', 'product_detail', 'cart_view'):
        for line in (args.run / 'load_pair/controls' / (stream + '.jsonl')).read_text().splitlines():
            trace_id = json.loads(line).get('trace_id')
            if trace_id:
                generated.add(trace_id)
    query = {'bool': {'filter': [
        {'range': {'startTimeMillis': {'gte': int(start.timestamp() * 1000),
                                      'lt': int((end + timedelta(seconds=30)).timestamp() * 1000)}}},
        {'terms': {'traceID': sorted(generated)}}]}}
    spans, total = reader.scan(query)
    by_trace = defaultdict(dict)
    for span in spans:
        by_trace[span['traceID']][span['spanID']] = span
    bins = defaultdict(lambda: {'trace_ids': set(), 'client_ms': [], 'server_ms': []})
    examples = []
    for span in spans:
        if (span.get('process', {}).get('serviceName') != 'cart' or
                (span.get('tag') or {}).get('span@kind') != 'server'):
            continue
        for ref in span.get('references') or []:
            parent = by_trace[span['traceID']].get(ref.get('spanID'))
            if not parent or parent.get('process', {}).get('serviceName') != 'checkout':
                continue
            minute = int((parent['startTimeMillis'] / 1000 - start.timestamp()) // 60)
            if not 0 <= minute < 5:
                continue
            values = bins[minute]
            values['trace_ids'].add(span['traceID'])
            values['client_ms'].append(parent.get('duration', 0) / 1000)
            values['server_ms'].append(span.get('duration', 0) / 1000)
            if len(examples) < 6:
                examples.append({'trace_id': span['traceID'],
                    'checkout_span_id': parent['spanID'], 'cart_span_id': span['spanID'],
                    'minute': minute, 'client_duration_ms': parent.get('duration', 0) / 1000})
            break
    rows = []
    for minute in range(5):
        values = bins[minute]
        checkout = [step for step in checkout_results if
                    minute <= (datetime.fromisoformat(step['time_utc']).timestamp() -
                               start.timestamp()) / 60 < minute + 1]
        rows.append({'minute': minute, 'phase': 'before' if minute < 2 else
                     'during' if minute == 2 else 'after',
                     'verified_call_edge_trace_ids': len(values['trace_ids']),
                     'p95_checkout_cart_client_ms': quantile(values['client_ms'], .95),
                     'p95_cart_server_ms': quantile(values['server_ms'], .95),
                     'checkout_business_requests': len(checkout),
                     'checkout_business_success': sum(step['success'] for step in checkout),
                     'checkout_business_errors': sum(not step['success'] for step in checkout)})
    result = {'run_id': schedule['run_id'], 'trace_source': 'JAEGER_OPENSEARCH_STORAGE',
              'workload_plan_sha256': schedule['plan_sha256'],
              'source': 'normal state-machine/fixed-stream Trace IDs joined to raw checkout->cart parent-child Spans',
              'issued_trace_ids': len(generated), 'queried_spans': total,
              'minutes': rows, 'examples': examples,
              'minimum_target_edge_spans_per_minute': min(
                  row['verified_call_edge_trace_ids'] for row in rows)}
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    fig = px.line(pd.DataFrame(rows), x='minute', y='verified_call_edge_trace_ids',
                  markers=True, title='Verified checkout to cart calls by minute')
    fig.add_hline(y=30, line_dash='dash', line_color='#bc573e')
    fig.write_html(args.output / 'target-edge-calls.html', include_plotlyjs=True)
    fig = px.line(pd.DataFrame(rows), x='minute', y='p95_checkout_cart_client_ms',
                  markers=True, title='Checkout to cart client-span p95')
    fig.add_vrect(x0=1.5, x1=2.5, fillcolor='#e8ac6d', opacity=.22, line_width=0)
    fig.write_html(args.output / 'target-edge-latency.html', include_plotlyjs=True)
    print(json.dumps({'run_id': result['run_id'], 'counts': [
        row['verified_call_edge_trace_ids'] for row in rows]}))


if __name__ == '__main__':
    main()
