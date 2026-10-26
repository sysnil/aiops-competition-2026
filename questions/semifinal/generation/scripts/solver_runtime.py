"""Public-first reference solver for one registered query-visualization task."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from inspect_sources import SOURCES
from validate_independent import independent_answers, recompute


ROOT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def independent_chart(path, question, points):
    image = Image.new('RGB', (1280, 720), 'white')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 19)
    left, right, top, bottom = 100, 1190, 100, 595
    valid = [item['value'] for item in points if item['value'] is not None]
    low, high = min(0, min(valid)), max(valid)
    if high <= low:
        high = low + 1
    draw.text((left, 24), f"{question['question_id']} independent Public solve",
              font=font, fill='#1d3440')
    draw.line((left, top, left, bottom, right, bottom), fill='#475c67', width=3)
    for tick in range(5):
        y = bottom - (bottom - top) * tick / 4
        draw.line((left, y, right, y), fill='#dce4e5', width=1)
        draw.text((9, y - 12), f'{low + (high-low)*tick/4:.2f}', font=font, fill='#475c67')
    entities = question['query_spec']['filters']['entities']
    n = max(item['minute_index'] for item in points) + 1
    for index, entity in enumerate(entities):
        color = ('#148079', '#ca5341')[index % 2]
        rows = [item for item in points if item['series'] == entity]
        previous = None
        for row in rows:
            if row['value'] is None:
                previous = None
                continue
            x = left + (right-left) * row['minute_index'] / max(1, n-1)
            y = bottom - (bottom-top) * (row['value']-low) / (high-low)
            if previous:
                draw.line((*previous, x, y), fill=color, width=3)
            draw.ellipse((x-3, y-3, x+3, y+3), fill=color)
            previous = (x, y)
        draw.text((left, 625 + 26*index), entity, font=font, fill=color)
    draw.text((right-210, 25), question['chart_requirement']['y_axis'],
              font=font, fill='#475c67')
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def run(question_id, root=ROOT):
    if not (question_id.startswith('Q') or question_id.startswith('EX-P')):
        raise ValueError('Unknown question ID')
    folder = 'practice-examples' if question_id.startswith('EX-P') else 'questions'
    question = json.loads((root / 'public' / folder / f'{question_id}.json').read_text())
    if question['question_id'] != question_id:
        raise ValueError('Question ID mismatch')
    query = question['query_spec']
    spec = {'id': question_id, 'case': question['case_id'],
            'kind': query['kind'], 'answer_plan': query['answer_plan'],
            'entities': query['filters']['entities'],
            'compare': query['comparison_minute_ranges']}
    if query['filters'].get('operation_name'):
        spec['operation'] = query['filters']['operation_name']
    public_root = SOURCES[question['case_id']]
    manifest = json.loads((public_root / 'manifest.json').read_text())
    if (question['time_range']['start_utc'] != manifest['window']['start_utc'] or
            question['time_range']['end_exclusive_utc'] != manifest['window']['end_utc']):
        raise ValueError('Question window does not match Public Manifest')
    points = recompute(public_root, manifest, spec)
    answers = independent_answers(spec, points)
    chart_path = root / 'validation/reference-solver-charts' / f'{question_id}.png'
    independent_chart(chart_path, question, points)
    solution_path = root / 'validation/reference-solvers' / f'{question_id}-public-solution.json'
    solution = {'question_id': question_id, 'case_id': question['case_id'],
                'source_public_manifest_sha256': sha(public_root / 'manifest.json'),
                'independent_chart_sha256': sha(chart_path),
                'answers_before_gt_read': answers, 'chart_points': len(points)}
    solution_path.write_text(json.dumps(solution, ensure_ascii=False, indent=2) + '\n')
    gt = json.loads((root / 'private/ground-truth' / f'{question_id}-gt.json').read_text())
    if gt['source_public_manifest_sha256'] != solution['source_public_manifest_sha256']:
        raise ValueError('Private GT source hash mismatch')
    for value, gold in zip(answers, gt['three_gold_answers']):
        if gold['answer_type'] == 'number':
            digits = 4 if gold['answer_unit'] == 'cores' else 0 if gold['numerical_tolerance'] == 0 else 2
            value = round(value, digits)
        if value != gold['answer']:
            raise ValueError(f'{question_id}: public solution differs from GT')
    result = {'question_id': question_id, 'status': 'PUBLIC_FIRST_SOLVER_PASS',
              'public_solution_sha256': sha(solution_path),
              'chart_sha256': sha(chart_path)}
    (root / 'validation/reference-solvers' / f'{question_id}-check.json').write_text(
        json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    raise SystemExit('Run a registered per-question wrapper')
