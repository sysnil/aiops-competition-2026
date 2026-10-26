"""Run every proposed chart query without creating question artifacts."""

import json

from inspect_sources import SOURCES
from public_series import build_series
from question_specs import QUESTIONS, PRACTICE


def main():
    results = []
    for spec in QUESTIONS + PRACTICE:
        root = SOURCES[spec['case']]
        manifest = json.loads((root / 'manifest.json').read_text())
        chart = build_series(root, manifest, spec)
        series = []
        for entity in spec['entities']:
            points = [row for row in chart['points'] if row['series'] == entity]
            valid = [row for row in points if row['value'] is not None]
            series.append({'entity': entity, 'points': len(points), 'valid': len(valid),
                           'min': min((row['value'] for row in valid), default=None),
                           'max': max((row['value'] for row in valid), default=None),
                           'samples': sum(row['sample_count'] for row in points)})
        results.append({'question_id': spec['id'], 'case_id': spec['case'],
                        'kind': spec['kind'], 'unit': chart['unit'],
                        'series': series, 'evidence_examples': len(chart['supporting_public_records'])})
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    main()
