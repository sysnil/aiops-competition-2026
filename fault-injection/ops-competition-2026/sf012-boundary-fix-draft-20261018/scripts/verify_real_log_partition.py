"""Read-only conversion check on retained SF-012 log pages, never a case export."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path('/root/ops-competition-2026')
RUN = Path('/data/stage5-v3/ops-competition-2026/runs/SF-012-ATTEMPT-02')
sys.path.insert(0, str(ROOT / 'sf012-pod300-adapter-v3-20261018/scripts'))
from sf003_export import log_partition
from pilot_exchange_schema import iso_ns, log_record


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / 'sf012-boundary-fix-draft-20261018/real-log-partition-diagnostic.json'
    if output.exists():
        raise RuntimeError('Diagnostic already exists')
    totals = Counter()
    examples = []
    for chunk in sorted((RUN / 'capture').glob('chunk-*')):
        first = chunk / 'captures/during'
        manifest = json.loads((first / 'capture-manifest.json').read_text())
        lo = iso_ns(manifest['window']['start_utc'])
        hi = iso_ns(manifest['window']['end_utc'])
        for page in sorted((first / 'logs').glob('*.json')):
            for hit in json.loads(page.read_text())['hits']['hits']:
                timestamp = log_record(hit['_source'])['timestamp']
                if log_partition(timestamp, lo, hi):
                    totals['in_window'] += 1
                else:
                    totals['excluded_query_boundary_neighbor'] += 1
                    examples.append({'chunk': chunk.name, 'index': hit['_index'],
                                     'document_id': hit['_id'],
                                     'timestamp_ns': timestamp})
    result = {'status': 'DIAGNOSTIC_ONLY_NOT_OFFICIAL_PUBLIC',
              'at_utc': datetime.now(timezone.utc).isoformat(),
              'run_id_read_only': 'SF-012-ATTEMPT-02',
              'draft_converter_sha256': sha(ROOT / 'sf012-pod300-adapter-v3-20261018/scripts/sf003_export.py'),
              'counts': dict(totals), 'excluded_examples': examples,
              'historical_case_status_unchanged': 'FAILED_PUBLIC_EXPORT_BOUNDARY'}
    with output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'status': result['status'], 'counts': result['counts'],
                      'sha256': sha(output)}))


if __name__ == '__main__':
    main()
