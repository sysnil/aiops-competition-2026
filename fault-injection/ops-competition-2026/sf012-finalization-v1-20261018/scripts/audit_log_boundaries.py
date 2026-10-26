"""Read-only audit of OpenSearch log query boundaries in SF-012."""

from collections import Counter
import json
from pathlib import Path
import sys


sys.path.insert(0, '/root/ops-competition-2026/sf012-pod300-adapter-v2-20261018/scripts')
from pilot_exchange_schema import iso_ns, log_record


RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-02')


def main():
    totals = Counter()
    examples = []
    for chunk in sorted((RUN / 'capture').glob('chunk-*')):
        first = chunk / 'captures/during'
        manifest = json.loads((first / 'capture-manifest.json').read_text())
        lo = iso_ns(manifest['window']['start_utc'])
        hi = iso_ns(manifest['window']['end_utc'])
        for page in sorted((first / 'logs').glob('*.json')):
            for hit in json.loads(page.read_text())['hits']['hits']:
                record = log_record(hit['_source'])
                ts = record['timestamp']
                if lo <= ts < hi:
                    totals[(chunk.name, 'in_window')] += 1
                else:
                    side = 'before' if ts < lo else 'after'
                    totals[(chunk.name, side)] += 1
                    if len(examples) < 20:
                        examples.append({'chunk': chunk.name, 'page': page.name,
                            'index': hit['_index'], 'id': hit['_id'],
                            'source_time': hit['_source'].get('@timestamp'),
                            'timestamp_ns': ts, 'offset_ns': ts - (lo if side == 'before' else hi),
                            'side': side})
    print(json.dumps({'status': 'READ_ONLY_LOG_BOUNDARY_AUDIT',
                      'counts': {f'{a}/{b}': n for (a, b), n in sorted(totals.items())},
                      'examples': examples}, indent=2))


if __name__ == '__main__':
    main()
