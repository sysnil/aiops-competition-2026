"""Independent fail-closed check of all trace-scroll windows in a no-fault run."""

import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(run, contract):
    schedule = json.loads((run / 'schedule.json').read_text())
    if schedule['plan_sha256'] != sha(contract) or schedule['faults_injected'] != 0:
        raise RuntimeError('Run is not bound to this no-fault contract')
    windows = sorted(schedule['windows'])
    if windows != [f'chunk-{index:02d}' for index in range(6)]:
        raise RuntimeError('Not six complete registered windows')
    chunks = []
    for window in windows:
        base = run / 'capture' / window
        first = base / 'captures/during/capture-manifest.json'
        plus60 = base / 'trace-completion/during/completion-manifest.json'
        final = base / 'trace-final/during/final-manifest.json'
        audit = base / 'trace-late-audit/during/late-audit-manifest.json'
        manifests = [json.loads(path.read_text()) for path in (first, plus60, final, audit)]
        logs = ''.join((run / f'{window}-{phase}.stderr').read_text()
                       for phase in ('first', 'plus60', 'audit'))
        if ('HTTP Error 429' in logs or '"http_status": 429' in logs or
                manifests[0]['trace_source'] != 'JAEGER_OPENSEARCH_STORAGE' or
                manifests[2]['first_manifest_sha256'] != sha(first) or
                manifests[3]['final_manifest_sha256'] != sha(final) or
                manifests[3]['late_count'] != 0):
            raise RuntimeError(f'Trace read, completion or audit failed for {window}')
        chunks.append({'window': window, 'first_spans': manifests[0]['span_records'],
                       'final_spans': manifests[2]['final_ids'],
                       'first_manifest_sha256': sha(first),
                       'final_manifest_sha256': sha(final)})
    observed = []
    with (run / 'monitor.jsonl').open() as stream:
        for line in stream:
            nodes = json.loads(line)['opensearch']['nodes']
            if len(nodes) != 1:
                raise RuntimeError('OpenSearch node identity changed')
            observed.append(next(iter(nodes.values()))['breakers']['parent']['tripped'])
    if not observed or max(observed) != min(observed):
        raise RuntimeError('OpenSearch parent circuit breaker tripped during run')
    return {'status': 'TRACE_READER_1800S_PASS', 'run': str(run),
            'contract_sha256': sha(contract), 'chunks': chunks,
            'opensearch_parent_breaker_start': observed[0],
            'opensearch_parent_breaker_end': observed[-1],
            'http_429_count': 0}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True, type=Path)
    parser.add_argument('--contract', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError('Health audit already exists')
    result = check(args.run, args.contract)
    with args.output.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps(result))
