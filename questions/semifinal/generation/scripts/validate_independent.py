"""Independent streaming recomputation of every candidate chart and answer."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path

from PIL import Image
import pyarrow.parquet as pq

from inspect_sources import SOURCES
from question_specs import QUESTIONS, PRACTICE


OUTPUT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def date(value):
    item = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    return item.replace(tzinfo=timezone.utc) if item.tzinfo is None else item.astimezone(timezone.utc)


def minute(ts, start, n):
    index = int((date(ts) - start).total_seconds() // 60)
    return index if 0 <= index < n else None


def rows(path, columns):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=5000, columns=columns):
        yield from batch.to_pylist()


def recompute(root, manifest, spec):
    start, end = date(manifest['window']['start_utc']), date(manifest['window']['end_utc'])
    n = int((end - start).total_seconds() // 60)
    names = spec['entities']
    kind = spec['kind']
    bins = {name: defaultdict(list) for name in names}
    if kind in ('memory', 'cpu_rate'):
        metric = 'container_memory_working_set_bytes' if kind == 'memory' else \
                 'container_cpu_usage_seconds_total'
        series = {name: [] for name in names}
        for row in rows(root / 'metrics.parquet',
                        ['timestamp', 'metric_name', 'pod', 'value', 'labels']):
            if row['metric_name'] == metric and row['pod'] in series and \
                    json.loads(row['labels'] or '{}').get('container') == 'frontend':
                ts = date(row['timestamp'])
                if start <= ts < end:
                    series[row['pod']].append((ts, float(row['value'])))
        for name, samples in series.items():
            samples.sort()
            previous = None
            for ts, value in samples:
                bucket = minute(ts, start, n)
                if kind == 'memory':
                    bins[name][bucket].append(value / 1048576)
                elif previous:
                    elapsed = (ts - previous[0]).total_seconds()
                    delta = value - previous[1]
                    if elapsed > 0 and delta >= 0:
                        bins[name][bucket].append(delta / elapsed)
                previous = (ts, value)
    elif kind == 'error_log_count':
        for row in rows(root / 'logs.parquet', ['timestamp', 'service', 'severity']):
            if row['service'] in bins and str(row['severity']).upper() == 'ERROR':
                bucket = minute(row['timestamp'], start, n)
                if bucket is not None:
                    bins[row['service']][bucket].append(1)
    else:
        edge = kind.startswith('edge_')
        pair = tuple(names[0].split(' -> ', 1)) if edge else None
        for row in rows(root / 'traces.parquet',
                        ['start_time', 'service', 'parent_service', 'operation_name',
                         'duration_ms', 'status', 'attributes']):
            if json.loads(row['attributes'] or '{}').get('span.kind', '').lower() != 'server':
                continue
            if edge:
                if (row['parent_service'], row['service']) != pair:
                    continue
                name = names[0]
            else:
                name = row['service']
                if name not in bins:
                    continue
            if spec.get('operation') and row['operation_name'] != spec['operation']:
                continue
            if kind == 'edge_error_count' and str(row['status']).upper() != 'ERROR':
                continue
            bucket = minute(row['start_time'], start, n)
            if bucket is None:
                continue
            value = row['duration_ms'] if kind.endswith('_p95') else 1
            if kind.endswith('_p95') and (value is None or not math.isfinite(value) or value < 0):
                continue
            bins[name][bucket].append(value)
    result = []
    for name in names:
        for index in range(n):
            samples = bins[name][index]
            if kind in ('memory', 'cpu_rate'):
                value = sum(samples) / len(samples) if len(samples) >= 2 else None
            elif kind.endswith('_p95'):
                sample = sorted(samples)
                value = sample[math.ceil(.95 * len(sample)) - 1] if len(sample) >= 20 else None
            else:
                value = float(len(samples))
            result.append({'series': name, 'minute_index': index,
                           'timestamp_utc': (start + timedelta(minutes=index)).isoformat(),
                           'value': value, 'sample_count': len(samples)})
    return result


def independent_answers(spec, points):
    grouped = {name: [row for row in points if row['series'] == name]
               for name in spec['entities']}
    first = grouped[spec['entities'][0]]
    valid = [row for row in first if row['value'] is not None]
    peak = max(valid, key=lambda row: row['value'])
    avg = lambda rows: sum(row['value'] for row in rows if row['value'] is not None) / \
                       sum(row['value'] is not None for row in rows)
    plan = spec['answer_plan']
    if plan == 'basic_mean':
        return [peak['value'], peak['timestamp_utc'], avg(first)]
    if plan == 'basic_total':
        return [int(sum(row['value'] for row in first)), int(peak['value']),
                peak['timestamp_utc']]
    if plan in ('window_count', 'window_mean'):
        a, b = spec['compare']
        left = first[a[0]:a[1]]
        right = first[b[0]:b[1]]
        if plan == 'window_count':
            return [int(sum(row['value'] for row in first)), peak['timestamp_utc'],
                    int(sum(row['value'] for row in right) - sum(row['value'] for row in left))]
        return [peak['value'], peak['timestamp_utc'], avg(right) - avg(left)]
    if plan == 'compare':
        a, b = (grouped[name] for name in spec['entities'])
        ma, mb = avg(a), avg(b)
        gaps = [(abs(x['value'] - y['value']), x['timestamp_utc']) for x, y in zip(a, b)
                if x['value'] is not None and y['value'] is not None]
        return [spec['entities'][0] if ma > mb else spec['entities'][1],
                abs(ma - mb), max(gaps, key=lambda row: row[0])[1]]
    raise ValueError(f'Unknown answer plan: {plan}')


def validate(output=OUTPUT):
    question_manifest = json.loads((output / 'public/question-manifest.json').read_text())
    if (len(question_manifest.get('formal_questions', [])) != 10 or
            len(question_manifest.get('practice_examples', [])) != 2 or
            {row['question_id'] for row in question_manifest['formal_questions']} !=
            {spec['id'] for spec in QUESTIONS} or
            {row['question_id'] for row in question_manifest['practice_examples']} !=
            {spec['id'] for spec in PRACTICE}):
        raise ValueError('Formal/practice question manifest mismatch')
    for section, folder in (('formal_questions', 'questions'),
                            ('practice_examples', 'practice-examples')):
        for row in question_manifest[section]:
            if digest(output / 'public' / folder / f"{row['question_id']}.json") != \
                    row['question_sha256']:
                raise ValueError(f"Question hash mismatch: {row['question_id']}")
    checks = []
    for spec in QUESTIONS + PRACTICE:
        root = SOURCES[spec['case']]
        public_folder = 'practice-examples' if spec in PRACTICE else 'questions'
        question = json.loads((output / 'public' / public_folder / f"{spec['id']}.json").read_text())
        gt = json.loads((output / 'private/ground-truth' / f"{spec['id']}-gt.json").read_text())
        manifest = json.loads((root / 'manifest.json').read_text())
        if (question['case_id'] != spec['case'] or gt['case_id'] != spec['case'] or
                gt['source_public_manifest_sha256'] != digest(root / 'manifest.json') or
                gt['source_file_sha256'] != digest(root / question['query_spec']['source_file']) or
                gt['review_status'] != 'DRAFT_PENDING_HUMAN_REVIEW' or
                len(question['questions']) != 3 or len(gt['three_gold_answers']) != 3 or
                gt['grading_rubric']['chart_points'] + 3 * gt['grading_rubric']['answer_points_each'] != 10):
            raise ValueError(f"{spec['id']}: metadata, GT, or point-total mismatch")
        if spec in QUESTIONS and any(key in question for key in
                ('three_gold_answers', 'canonical_chart_data', 'review_status',
                 'grading_rubric', 'source_public_manifest_sha256')):
            raise ValueError(f"{spec['id']}: private key in Public question")
        actual = recompute(root, manifest, spec)
        expected = gt['canonical_chart_data']
        if len(actual) != len(expected):
            raise ValueError(f"{spec['id']}: chart length differs")
        for left, right in zip(actual, expected):
            if any(left[key] != right[key] for key in
                    ('series', 'minute_index', 'timestamp_utc', 'sample_count')):
                raise ValueError(f"{spec['id']}: chart identity/sample mismatch")
            a, b = left['value'], right['value']
            if (a is None) != (b is None) or \
                    (a is not None and not math.isclose(a, b, abs_tol=1e-9, rel_tol=0)):
                raise ValueError(f"{spec['id']}: chart value mismatch")
        answers = independent_answers(spec, actual)
        for value, gold in zip(answers, gt['three_gold_answers']):
            if gold['answer_type'] == 'number':
                digits = 4 if gold['answer_unit'] == 'cores' else 0 if gold['numerical_tolerance'] == 0 else 2
                value = round(value, digits)
            if value != gold['answer']:
                raise ValueError(f"{spec['id']}: answer mismatch: {value} != {gold['answer']}")
        chart = output / gt['reference_chart_path']
        with Image.open(chart) as image:
            image.verify()
        checks.append({'question_id': spec['id'], 'case_id': spec['case'],
                       'status': 'PUBLIC_RECOMPUTATION_PASS',
                       'chart_points': len(actual), 'answers': answers})
    report = output / 'REPORT.md'
    text = report.read_text()
    if ('Candidate status: PENDING_INDEPENDENT_VALIDATION.' not in text and
            'READY_FOR_HUMAN_REVIEW' not in text):
        raise ValueError('Unexpected report state')
    if 'Candidate status: PENDING_INDEPENDENT_VALIDATION.' in text:
        report.write_text(text.replace('Candidate status: PENDING_INDEPENDENT_VALIDATION.',
                                       'Candidate status: READY_FOR_HUMAN_REVIEW.'))
    hashes = {}
    for path in sorted(output.rglob('*')):
        if path.is_file() and path.name != 'sha256-manifest.json' and \
                not path.is_relative_to(output / 'validation') and \
                not path.is_relative_to(output / 'scripts'):
            hashes[str(path.relative_to(output))] = digest(path)
    (output / 'sha256-manifest.json').write_text(json.dumps(
        {'version': 'semifinal-query-visualization-candidate-v1', 'files': hashes},
        indent=2) + '\n')
    result = {'status': 'READY_FOR_HUMAN_REVIEW', 'formal_count': len(QUESTIONS),
              'practice_example_count': len(PRACTICE), 'checks': checks}
    (output / 'validation/reproducibility-results.json').write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + '\n')
    return result


if __name__ == '__main__':
    print(json.dumps(validate(), ensure_ascii=False))
