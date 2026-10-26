"""Build Public questions and separate draft GT from frozen Public data."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from inspect_sources import SOURCES, inspect
from public_series import build_series, FILES, UNITS
from question_specs import QUESTIONS, PRACTICE


ROOT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')
VERSION = 'semifinal-query-visualization-candidate-v1'


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def topology_check(root, spec):
    topology = json.loads((root / 'topology.json').read_text())
    pods = [pod for snapshot in topology['snapshots'] for pod in snapshot['pods']]
    services = {pod['service'] for pod in pods}
    if spec['kind'] in ('memory', 'cpu_rate'):
        for name in spec['entities']:
            uids = {pod['uid'] for pod in pods if pod['name'] == name}
            if len(uids) != 1:
                raise ValueError(f"{spec['id']}: Pod does not have one Public UID: {name}")
    elif spec['kind'].startswith('edge_'):
        src, dst = spec['entities'][0].split(' -> ', 1)
        edges = {(row['source'], row['destination'])
                 for row in topology.get('observed_service_edges', [])}
        if (src, dst) not in edges:
            raise ValueError(f"{spec['id']}: edge is not in Public topology")
    elif not set(spec['entities']).issubset(services):
        raise ValueError(f"{spec['id']}: target Service is not in Public topology")


def prepare(spec, inventory):
    case, root = spec['case'], SOURCES[spec['case']]
    manifest = json.loads((root / 'manifest.json').read_text())
    if inventory[case]['manifest_sha256'] != sha(root / 'manifest.json'):
        raise ValueError(f'{case}: Manifest changed after inventory')
    topology_check(root, spec)
    chart = build_series(root, manifest, spec)
    minutes = int((__import__('pandas').Timestamp(manifest['window']['end_utc']) -
                   __import__('pandas').Timestamp(manifest['window']['start_utc'])).total_seconds() / 60)
    if len(chart['points']) != minutes * len(spec['entities']):
        raise ValueError(f"{spec['id']}: incomplete chart axis")
    for entity in spec['entities']:
        points = [row for row in chart['points'] if row['series'] == entity]
        valid = [row for row in points if row['value'] is not None]
        if len(valid) < math.ceil(.8 * minutes) or sum(row['sample_count'] for row in points) < 5:
            raise ValueError(f"{spec['id']}: target has insufficient real samples")
        if spec['kind'].endswith('_p95') and any(row['sample_count'] < 20 for row in valid):
            raise ValueError(f"{spec['id']}: p95 minute has fewer than 20 spans")
    if spec['kind'] in ('error_log_count', 'edge_error_count') and \
            not any(row['value'] for row in chart['points']):
        raise ValueError(f"{spec['id']}: error-count chart is all zero")
    return manifest, chart


def _single(chart):
    return chart['points']


def _valid(rows):
    result = [row for row in rows if row['value'] is not None]
    if not result:
        raise ValueError('No valid chart values')
    return result


def _peak(rows):
    return max(_valid(rows), key=lambda row: row['value'])


def _mean(rows):
    values = [row['value'] for row in _valid(rows)]
    return sum(values) / len(values)


def _answer(kind, value, unit, method):
    if kind == 'number':
        if unit == 'cores':
            rounded, tolerance = round(value, 4), 0.0002
        elif unit in ('spans', 'logs', 'error spans'):
            rounded, tolerance = int(value), 0
        else:
            rounded, tolerance = round(value, 2), 0.02
    else:
        rounded, tolerance = value, None
    return {'answer_type': kind, 'answer': rounded, 'answer_unit': unit,
            'numerical_tolerance': tolerance, 'time_tolerance_seconds': 0,
            'calculation_method': method}


def make_answers(spec, chart, start):
    plan, unit = spec['answer_plan'], chart['unit']
    entities = spec['entities']
    rows = [row for row in chart['points'] if row['series'] == entities[0]]
    peak = _peak(rows)
    answers, prompts = [], []
    label = '每分钟均值' if spec['kind'] in ('memory', 'cpu_rate') else \
            '每分钟 p95' if spec['kind'].endswith('_p95') else '每分钟数量'

    if plan == 'basic_mean':
        prompts = [f'整段时间内，最高的{label}是多少（单位 {unit}）？',
                   '最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。',
                   f'整段时间内所有有效分钟值的算术平均是多少（单位 {unit}）？']
        answers = [_answer('number', peak['value'], unit, 'max of valid minute values'),
                   _answer('time', peak['timestamp_utc'], 'UTC', 'earliest maximum minute start'),
                   _answer('number', _mean(rows), unit, 'unweighted mean of valid minute values')]
    elif plan == 'basic_total':
        count_unit = 'logs' if spec['kind'] == 'error_log_count' else 'spans'
        item_zh = 'ERROR 日志' if spec['kind'] == 'error_log_count' else '服务端 Span'
        prompts = [f'整段时间内共有多少条符合条件的 {item_zh}？',
                   f'数量最多的一分钟有多少条 {item_zh}？',
                   '数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。']
        answers = [_answer('number', sum(row['value'] for row in rows), count_unit,
                           'sum of all 60-second bucket counts'),
                   _answer('number', peak['value'], count_unit, 'maximum minute count'),
                   _answer('time', peak['timestamp_utc'], 'UTC', 'earliest maximum minute start')]
    elif plan in ('window_mean', 'window_count'):
        first, second = spec['compare']
        a = [row for row in rows if first[0] <= row['minute_index'] < first[1]]
        b = [row for row in rows if second[0] <= row['minute_index'] < second[1]]
        if len(_valid(a)) != first[1] - first[0] or len(_valid(b)) != second[1] - second[0]:
            raise ValueError(f"{spec['id']}: comparison window has missing minutes")
        a_time = (start + timedelta(minutes=first[0])).strftime('%H:%M:%S')
        b_time = (start + timedelta(minutes=second[0])).strftime('%H:%M:%S')
        if plan == 'window_count':
            count_unit = 'error spans'
            prompts = [f'整段时间内共有多少条错误调用边 Span（单位 {count_unit}）？',
                       '错误 Span 数量最多的分钟起点 UTC 是什么？若并列，取最早一分钟。',
                       f'从 {b_time} UTC 起的 5 分钟总数减去从 {a_time} UTC 起的 5 分钟总数，差是多少（单位 {count_unit}）？']
            answers = [_answer('number', sum(row['value'] for row in rows), count_unit,
                               'sum of all minute error-span counts'),
                       _answer('time', peak['timestamp_utc'], 'UTC', 'earliest maximum minute start'),
                       _answer('number', sum(row['value'] for row in b) - sum(row['value'] for row in a),
                               count_unit, 'second five-minute count minus first five-minute count')]
        else:
            prompts = [f'整段时间内，最高的每分钟 p95 是多少（单位 {unit}）？',
                       '最高值出现在哪个 UTC 分钟起点？若并列，取最早一分钟。',
                       f'从 {b_time} UTC 起的 5 个分钟 p95 的均值，减去从 {a_time} UTC 起的 5 个分钟 p95 的均值，差是多少（单位 {unit}）？']
            answers = [_answer('number', peak['value'], unit, 'max of minute nearest-rank p95 values'),
                       _answer('time', peak['timestamp_utc'], 'UTC', 'earliest maximum minute start'),
                       _answer('number', _mean(b) - _mean(a), unit,
                               'unweighted mean of five p95 values: second window minus first')]
    elif plan == 'compare':
        left, right = entities
        a = [row for row in chart['points'] if row['series'] == left]
        b = [row for row in chart['points'] if row['series'] == right]
        ma, mb = _mean(a), _mean(b)
        higher = left if ma > mb else right
        gap = max(((abs(x['value'] - y['value']), x['timestamp_utc'])
                   for x, y in zip(a, b) if x['value'] is not None and y['value'] is not None),
                  key=lambda item: item[0])
        prompts = [f'哪一个实体的整段有效分钟值均值更高：{left} 还是 {right}？',
                   f'两条曲线的整段分钟均值相差多少（绝对值，单位 {unit}）？',
                   '两条曲线的差距（绝对值）最大时，UTC 分钟起点是什么？若并列，取最早一分钟。']
        answers = [_answer('entity', higher, 'entity ID', 'compare unweighted means of valid minutes'),
                   _answer('number', abs(ma - mb), unit, 'absolute difference of per-series minute means'),
                   _answer('time', gap[1], 'UTC', 'earliest maximum absolute per-minute difference')]
    else:
        raise ValueError(f'Unknown answer plan: {plan}')
    return [{'id': f'A{index}', 'text': text,
             'answer_format': answers[index - 1]['answer_type']}
            for index, text in enumerate(prompts, 1)], answers


def query_spec(spec, chart, manifest):
    kind = spec['kind']
    filters = {'entities': spec['entities']}
    if kind == 'memory':
        filters.update(metric_name='container_memory_working_set_bytes', container='frontend')
        aggregation = 'mean(value) / 1048576 by 60-second bucket'
    elif kind == 'cpu_rate':
        filters.update(metric_name='container_cpu_usage_seconds_total', container='frontend')
        aggregation = 'mean(max(0, counter_delta) / elapsed_seconds) by later-sample 60-second bucket'
    elif kind == 'error_log_count':
        filters.update(severity='ERROR (case-insensitive exact match)')
        aggregation = 'count matching log records by 60-second bucket'
    else:
        filters.update(span_kind='server')
        if spec.get('operation'):
            filters['operation_name'] = spec['operation']
        if kind == 'edge_error_count':
            filters['status'] = 'ERROR (case-insensitive exact match)'
        aggregation = ('nearest-rank p95 of valid duration_ms in each bucket'
                       if kind.endswith('_p95') else 'count matching server Spans by 60-second bucket')
    return {'kind': kind, 'answer_plan': spec['answer_plan'],
        'source_file': chart['source_file'], 'source_columns': {
        'memory': ['timestamp', 'metric_name', 'value', 'pod', 'labels'],
        'cpu_rate': ['timestamp', 'metric_name', 'value', 'pod', 'labels'],
        'error_log_count': ['timestamp', 'service', 'severity'],
    }.get(kind, ['start_time', 'service', 'parent_service', 'operation_name',
                 'duration_ms', 'status', 'attributes']),
        'filters': filters, 'aggregation': aggregation,
        'time_bucket': {'origin_utc': manifest['window']['start_utc'],
                        'width_seconds': 60, 'closed': 'left',
                        'timestamp_label': 'bucket start UTC',
                        'observation_end_exclusive_utc': manifest['window']['end_utc']},
        'minimum_samples_per_minute': 20 if kind.endswith('_p95') else
            2 if kind in ('memory', 'cpu_rate') else 0,
        'missing_policy': 'null and line gap for insufficient samples; zero only for observed count queries',
        'counter_reset_policy': 'omit negative deltas and nonpositive time gaps' if kind == 'cpu_rate'
                                else 'not applicable',
        'p95_definition': 'sort ascending; rank ceil(0.95*n), one-based' if kind.endswith('_p95')
                          else 'not applicable',
        'comparison_minute_ranges': spec.get('compare')}


def draw_chart(path: Path, spec, chart):
    image = Image.new('RGB', (1600, 900), '#ffffff')
    draw = ImageDraw.Draw(image)
    font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    title = ImageFont.truetype(font, 31)
    body = ImageFont.truetype(font, 22)
    small = ImageFont.truetype(font, 18)
    left, right, top, bottom = 132, 1510, 170, 740
    valid = [row['value'] for row in chart['points'] if row['value'] is not None]
    low = min(0, min(valid)) if spec['kind'] not in ('memory',) else min(valid) * .98
    high = max(valid)
    if high == low:
        high = low + 1
    high += (high - low) * .08
    draw.text((left, 40), f"{spec['id']}  {spec['title']}", font=title, fill='#172c37')
    draw.text((left, 91), f"{spec['case']}  |  {chart['unit']}  |  UTC minute start", font=body,
              fill='#52636c')
    for index in range(6):
        y = bottom - (bottom - top) * index / 5
        value = low + (high - low) * index / 5
        draw.line((left, y, right, y), fill='#dce4e5', width=2)
        draw.text((16, y - 12), f'{value:,.2f}', font=small, fill='#51636b')
    draw.line((left, top, left, bottom), fill='#415860', width=3)
    draw.line((left, bottom, right, bottom), fill='#415860', width=3)
    count = max(row['minute_index'] for row in chart['points']) + 1
    tick = 5 if count <= 30 else 10
    for minute in range(0, count, tick):
        x = left + (right - left) * minute / max(1, count - 1)
        label = chart['points'][minute]['timestamp_utc'][11:16]
        draw.line((x, bottom, x, bottom + 9), fill='#415860', width=2)
        draw.text((x - 29, bottom + 17), label, font=small, fill='#415860')
    colors = ['#147f79', '#c94f3c', '#4f75a8']
    for index, entity in enumerate(spec['entities']):
        color = colors[index % len(colors)]
        points = [row for row in chart['points'] if row['series'] == entity]
        previous = None
        for row in points:
            if row['value'] is None:
                previous = None
                continue
            x = left + (right - left) * row['minute_index'] / max(1, count - 1)
            y = bottom - (bottom - top) * (row['value'] - low) / (high - low)
            if previous:
                draw.line((*previous, x, y), fill=color, width=4)
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
            previous = (x, y)
        draw.line((left + 15, 820 + 31 * index, left + 65, 820 + 31 * index),
                  fill=color, width=5)
        draw.text((left + 80, 805 + 31 * index), entity, font=small, fill='#314750')
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def build(output: Path, dry_run: bool):
    inventory = {case: inspect(case, root) for case, root in SOURCES.items()}
    prepared = []
    for spec in QUESTIONS + PRACTICE:
        manifest, chart = prepare(spec, inventory)
        questions, answers = make_answers(spec, chart,
            __import__('pandas').Timestamp(manifest['window']['start_utc']))
        prepared.append((spec, manifest, chart, questions, answers))
    if dry_run:
        return [{'question_id': spec['id'], 'case_id': spec['case'],
                 'valid_points': sum(row['value'] is not None for row in chart['points']),
                 'answers': answers}
                for spec, _, chart, _, answers in prepared]
    if (output / 'public').exists() or (output / 'private').exists():
        raise FileExistsError('Question output exists; no overwrite allowed')
    os.umask(0o077)
    public, private = output / 'public', output / 'private'
    manifest_rows, report_rows, review_rows, examples = [], [], [], []
    for spec, source, chart, questions, answers in prepared:
        practice = spec in PRACTICE
        folder = 'practice-examples' if practice else 'questions'
        public_path = public / folder / f"{spec['id']}.json"
        image_path = (public / 'manual-assets' / f"{spec['id']}.png" if practice else
                      private / 'reference-charts' / f"{spec['id']}.png")
        query = query_spec(spec, chart, source)
        question = {'question_id': spec['id'], 'case_id': spec['case'],
            'title': spec['title_zh'], 'description':
                '使用已归档的真实监控记录，按下列口径绘制一张分钟时序图并回答三个问题。',
            'target_entity_type': 'Pod' if spec['kind'] in ('memory', 'cpu_rate') else
                'CallEdge' if spec['kind'].startswith('edge_') else 'Service',
            'target_entity_id': spec['entities'][0] if len(spec['entities']) == 1 else spec['entities'],
            'modality': chart['modality'],
            'time_range': {'start_utc': source['window']['start_utc'],
                           'end_exclusive_utc': source['window']['end_utc']},
            'query_spec': query,
            'chart_requirement': {'chart_count': 1, 'type': 'line',
                'x_axis': 'UTC bucket start', 'y_axis': chart['unit'],
                'series': spec['entities'], 'granularity_seconds': 60,
                'legend_required': len(spec['entities']) > 1},
            'questions': questions, 'question_version': VERSION,
            'difficulty': spec['difficulty']}
        write_json(public_path, question)
        draw_chart(image_path, spec, chart)
        gt = {'question_id': spec['id'], 'case_id': spec['case'],
            'source_public_manifest_sha256': inventory[spec['case']]['manifest_sha256'],
            'source_file_sha256': source['files'][chart['source_file']]['sha256'],
            'canonical_chart_data': chart['points'],
            'reference_chart_path': str(image_path.relative_to(output)),
            'three_gold_answers': answers,
            'supporting_public_records': chart['supporting_public_records'],
            'grading_rubric': {'chart_points': 4, 'answer_points_each': 2,
                'chart_breakdown': {'entity_and_filter': 1, 'time_axis': 1,
                                    'values_and_aggregation': 1, 'unit_and_legend': 1}},
            'gt_version': VERSION, 'review_status': 'DRAFT_PENDING_HUMAN_REVIEW',
            'calculation_method': query}
        write_json(private / 'ground-truth' / f"{spec['id']}-gt.json", gt)
        manifest_rows.append({'question_id': spec['id'], 'case_id': spec['case'],
                              'classification': 'PRACTICE_EXAMPLE' if practice else 'FORMAL_CANDIDATE',
                              'question_sha256': sha(public_path)})
        answer_summary = '; '.join(f"{row['id']}={answer['answer']} {answer['answer_unit']}"
                                   for row, answer in zip(questions, answers))
        if practice:
            examples.append((spec, question, answers, image_path.relative_to(public)))
        else:
            report_rows.append(f"| {spec['id']} | {spec['case']} | {spec['title_zh']} | "
                               f"{', '.join(spec['entities'])} | {spec['kind']} | "
                               f"{spec['difficulty']} | {answer_summary} |")
            review_rows.append(f"### {spec['id']} {spec['title_zh']}\n\n"
                f"Case: {spec['case']}; target: {', '.join(spec['entities'])}; "
                f"window: {source['window']['start_utc']} to {source['window']['end_utc']} UTC.\n\n"
                + '\n'.join(f"{index}. {prompt['text']} **{answer['answer']} {answer['answer_unit']}**"
                            for index, (prompt, answer) in enumerate(zip(questions, answers), 1))
                + f"\n\n![Reference chart](reference-charts/{spec['id']}.png)\n\n"
                  "Review notes: ____________________\n")
    write_json(public / 'question-manifest.json',
               {'task_type': 'SEMIFINAL_QUERY_VISUALIZATION', 'version': VERSION,
                'formal_count': len(QUESTIONS), 'practice_example_count': len(PRACTICE),
                'formal_questions': [row for row in manifest_rows
                                     if row['classification'] == 'FORMAL_CANDIDATE'],
                'practice_examples': [row for row in manifest_rows
                                      if row['classification'] == 'PRACTICE_EXAMPLE']})
    manual = ['# 选手手册：查询与绘图练习示例', '',
        '以下两题仅使用 SF-001、SF-002 练习数据；答案已公开，不能作为正式 Q01～Q10 的答案。',
        '实际 Question API 尚未在本任务中核验，下面使用题目所列 Public 字段说明处理方法，'
        '不虚构接口 URL。图表均由练习 Public 原始记录生成。', '']
    for spec, question, answers, image in examples:
        manual.extend([f"## {spec['id']} · {spec['case']} · {spec['title_zh']}", '',
            f"实体：{', '.join(spec['entities'])}；数据：{FILES[spec['kind']]}；"
            f"窗口：{question['time_range']['start_utc']} 至 "
            f"{question['time_range']['end_exclusive_utc']}（右端不含）。", '',
            f"口径：{question['query_spec']['aggregation']}；"
            '每个时间点标记 60 秒桶的起点，空样本不当作零值。', '',
            f"![{spec['id']} 参考图](manual-assets/{image.name})", ''])
        for index, (prompt, answer) in enumerate(zip(question['questions'], answers), 1):
            manual.append(f"{index}. {prompt['text']} **{answer['answer']} {answer['answer_unit']}**。")
        manual.extend(['', '可按题面过滤 Public 记录、计算每分钟数值、绘制曲线，再从同一序列读取答案。', ''])
    (public / 'PLAYER_MANUAL_QUERY_EXAMPLES.md').write_text('\n'.join(manual) + '\n')
    (private / 'COMMITTEE_REVIEW.md').write_text(
        '# 组委会逐题审核表\n\n每题 1 张图、3 问、10 分；答案仅在私有审核目录。\n\n'
        + '\n'.join(review_rows) + '\n')
    report = ['# Semifinal query visualization candidate set', '',
        'Candidate status: PENDING_INDEPENDENT_VALIDATION.',
        f'Task type: SEMIFINAL_QUERY_VISUALIZATION. Formal 10; practice examples 2.',
        '', '| ID | Case | Title | Entity | Query | Difficulty | Private gold answers |',
        '| --- | --- | --- | --- | --- | --- | --- |', *report_rows, '',
        '## Source Public manifests', '']
    report.extend(f"- {case}: `{row['root']}`, SHA-256 `{row['manifest_sha256']}`; "
                  f"rows {row['rows']}" for case, row in inventory.items())
    report.extend(['', 'Every listed Public file passed Manifest SHA-256 and byte-size checks.',
                   'Private injection records were not read. Public questions contain no gold answer.',
                   'The 10 GT files remain DRAFT_PENDING_HUMAN_REVIEW and must not be published.',
                   'The actual 50/51 Question API schema and player manual source were not accessed.', ''])
    (output / 'REPORT.md').write_text('\n'.join(report))
    files = {}
    for path in sorted(output.rglob('*')):
        if (path.is_file() and path.name != 'sha256-manifest.json' and
                not path.is_relative_to(output / 'validation') and
                not path.is_relative_to(output / 'scripts')):
            files[str(path.relative_to(output))] = sha(path)
    write_json(output / 'sha256-manifest.json', {'version': VERSION, 'files': files})
    return [{'question_id': spec['id'], 'case_id': spec['case'], 'answers': answers}
            for spec, _, _, _, answers in prepared]


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.dry_run), ensure_ascii=False, indent=2))
