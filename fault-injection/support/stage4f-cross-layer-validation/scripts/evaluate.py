#!/usr/bin/env python3
"""Open private Stage4F GT only after both public and prediction archives verify."""

import argparse
import csv
import json
from pathlib import Path

import yaml

from admission import admit
from freeze_predictions import CASES, METHODS, NETWORK_A_METHODS, verify as verify_predictions


def score_prediction(result, gt):
    ranking = result.get('ranking') or []
    if not ranking:
        return {'layer_at_1': 0, 'entity_at_1': 0, 'entity_at_3': 0,
                'entity_at_1_conditioned_on_correct_layer': None,
                'end_to_end_exact': 0, 'mrr': 0.0, 'hierarchical_state': 'no_prediction'}
    layer = ranking[0]['level'] == gt['root_layer']
    entity = ranking[0]['entity'] == gt['root_entity']
    rank = next((item['rank'] for item in ranking if item['entity'] == gt['root_entity']), None)
    return {'layer_at_1': int(layer), 'entity_at_1': int(entity),
            'entity_at_3': int(rank is not None and rank <= 3),
            'entity_at_1_conditioned_on_correct_layer': int(entity) if layer else None,
            'end_to_end_exact': int(layer and entity), 'mrr': 1 / rank if rank else 0.0,
            'hierarchical_state': ('exact_entity' if entity else
                                   'correct_layer_wrong_entity' if layer else 'wrong_layer')}


def evaluate(root):
    verify_predictions(root)
    admissions = []
    rows = []
    for case in CASES:
        public_dir = root / 'cases' / case / 'public/variant-b'
        bundle = json.loads((public_dir / 'bundle.json').read_text())
        public_quality = json.loads((public_dir / 'case-quality.json').read_text())
        # This is the first evaluator GT read in the Stage4F pipeline.
        gt = yaml.safe_load((root / 'evaluator_gt' / case / 'ground_truth.yaml').read_text())
        private_quality = json.loads((root / 'evaluator_gt' / case /
                                      'admission-quality.json').read_text())
        quality = {**public_quality, **private_quality}
        precheck_name = ('pod-precheck.json' if gt['root_layer'] == 'POD' else
                         'network-path-precheck.json' if gt['root_layer'] == 'NETWORK_PATH' else None)
        precheck = None
        if precheck_name:
            path = root / 'preflight' / case / precheck_name
            if path.is_file():
                precheck = json.loads(path.read_text())
        admission = admit(case, bundle, quality, gt, precheck)
        admissions.append(admission)
        for method in METHODS:
            directory = root / 'runs' / case / 'variant-b' / method
            run = json.loads((directory / 'run-manifest.json').read_text())
            result = json.loads((directory / 'prediction.json').read_text())
            eligible = (admission['status'] == 'ADMITTED_CASE' and
                        run['status'] in ('PASS', 'NO_PREDICTION'))
            metrics = score_prediction(result, gt) if eligible else {
                'layer_at_1': None, 'entity_at_1': None, 'entity_at_3': None,
                'entity_at_1_conditioned_on_correct_layer': None,
                'end_to_end_exact': None, 'mrr': None, 'hierarchical_state': None}
            rows.append({'case_id': case, 'algorithm': method, 'variant': 'B',
                         'case_status': admission['status'], 'run_status': run['status'],
                         'candidate_space_supports_gt': gt['root_entity'] in {
                             item['entity'] for item in bundle['candidates']},
                         'root_layer': gt['root_layer'], 'root_entity': gt['root_entity'],
                         'top1': (result.get('ranking') or [{}])[0].get('entity'), **metrics})
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    (reports / 'case-admission.json').write_text(json.dumps(admissions, indent=2) + '\n')
    with (reports / 'method-case-results.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for method in METHODS:
        active = [row for row in rows if row['algorithm'] == method and row['case_status'] == 'ADMITTED_CASE'
                  and row['run_status'] in ('PASS', 'NO_PREDICTION')]
        correct_layer = [row for row in active if row['layer_at_1'] == 1]
        summary.append({'algorithm': method, 'admitted_runs': len(active),
                        'layer_at_1': sum(row['layer_at_1'] for row in active) / len(active) if active else None,
                        'entity_at_1_given_layer': (sum(row['entity_at_1'] for row in correct_layer) /
                                                    len(correct_layer)) if correct_layer else None,
                        'end_to_end_exact': (sum(row['end_to_end_exact'] for row in active) /
                                             len(active)) if active else None,
                        'entity_at_3': sum(row['entity_at_3'] for row in active) / len(active) if active else None,
                        'hierarchical_counts': {state: sum(row['hierarchical_state'] == state for row in active)
                                                for state in ('wrong_layer', 'correct_layer_wrong_entity',
                                                              'exact_entity', 'no_prediction')}})
    (reports / 'method-summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    variant_rows = []
    for case in CASES:
        case_gt = yaml.safe_load((root / 'evaluator_gt' / case / 'ground_truth.yaml').read_text())
        for method in NETWORK_A_METHODS:
            runs = {}
            for variant in ('variant-a', 'variant-b'):
                directory = root / 'runs' / case / variant / method
                runs[variant] = (json.loads((directory / 'run-manifest.json').read_text()),
                                 json.loads((directory / 'prediction.json').read_text()))
            a_run, a_prediction = runs['variant-a']
            b_run, b_prediction = runs['variant-b']
            a_universe = {item['entity'] for item in json.loads((root / 'cases' / case /
                          'public/variant-a/bundle.json').read_text())['candidates']}
            b_universe = {item['entity'] for item in json.loads((root / 'cases' / case /
                          'public/variant-b/bundle.json').read_text())['candidates']}
            if next(item for item in admissions if item['case_id'] == case)['status'] != 'ADMITTED_CASE':
                status = 'INVALID_CASE'
            elif b_run.get('network_context_consumed') is not True:
                status = 'NETWORK_CONTEXT_UNSUPPORTED'
            elif a_run['status'] != 'PASS' or b_run['status'] != 'PASS':
                status = 'RUN_NOT_COMPARABLE'
            else:
                status = 'BEHAVIOR_COMPARABLE'
            a_top = (a_prediction.get('ranking') or [{}])[0].get('entity')
            b_top = (b_prediction.get('ranking') or [{}])[0].get('entity')
            comparable = status == 'BEHAVIOR_COMPARABLE'
            variant_rows.append({'case_id': case, 'algorithm': method, 'status': status,
                                 'a_top1': a_top, 'b_top1': b_top,
                                 'top1_changed': a_top != b_top if comparable else None,
                                 'a_candidate_space_supports_gt': case_gt['root_entity'] in a_universe,
                                 'b_candidate_space_supports_gt': case_gt['root_entity'] in b_universe,
                                 'a_layer_at_1': (int(a_top.startswith(case_gt['root_layer'] + '/'))
                                                  if comparable and a_top else None),
                                 'b_layer_at_1': (int(b_top.startswith(case_gt['root_layer'] + '/'))
                                                  if comparable and b_top else None),
                                 'a_entity_at_1': (int(a_top == case_gt['root_entity'])
                                                   if comparable and a_top else None),
                                 'b_entity_at_1': (int(b_top == case_gt['root_entity'])
                                                   if comparable and b_top else None),
                                 'b_network_path_false_positive': (
                                     b_top.startswith('NETWORK_PATH/') and
                                     case_gt['root_layer'] != 'NETWORK_PATH')
                                     if comparable and b_top else None})
    with (reports / 'variant-a-vs-b.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(variant_rows[0]))
        writer.writeheader()
        writer.writerows(variant_rows)
    return {'admitted_cases': sum(item['status'] == 'ADMITTED_CASE' for item in admissions),
            'invalid_cases': [item['case_id'] for item in admissions if item['status'] == 'INVALID_CASE'],
            'methods': summary}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.root)))
