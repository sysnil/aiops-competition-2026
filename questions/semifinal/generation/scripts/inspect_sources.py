"""Read-only inventory of frozen Public competition bundles."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import pyarrow.parquet as pq


SOURCES = {
    'SF-001': Path('/data/stage5-v3/ops-competition-2026/runs/SF-PILOT-01-ATTEMPT-04/public'),
    'SF-002': Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002/public'),
    'SF-003': Path('/data/stage5-v3/ops-competition-2026/packages/SF-003-ATTEMPT-02/public'),
    'SF-005': Path('/data/stage5-v3/ops-competition-2026/packages/SF-005-ATTEMPT-04/public'),
    'SF-010': Path('/data/stage5-v3/ops-competition-2026/exports/SF-010-ATTEMPT-07/public'),
}


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def inspect(case_id: str, root: Path) -> dict:
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('case_id') != case_id:
        raise ValueError(f'{case_id}: case ID mismatch')
    for filename, info in manifest['files'].items():
        path = root / filename
        if checksum(path) != info['sha256'] or path.stat().st_size != info['bytes']:
            raise ValueError(f'{case_id}: Public file checksum/size mismatch: {filename}')
    metric_path = root / 'metrics.parquet'
    metrics = pq.read_table(metric_path, columns=['metric_name', 'labels'])
    names = Counter(metrics.column('metric_name').to_pylist())
    labels = [json.loads(row or '{}') for row in metrics.column('labels').to_pylist()]
    services = Counter(row.get('service') or row.get('service_name') for row in labels)
    pods = Counter(row.get('pod') or row.get('k8s_pod_name') for row in labels)
    topology = json.loads((root / 'topology.json').read_text())
    result = {'case_id': case_id, 'root': str(root),
              'manifest_sha256': checksum(manifest_path), 'window': manifest['window'],
              'rows': {name: pq.ParquetFile(root / name).metadata.num_rows
                       for name in ('metrics.parquet', 'logs.parquet',
                                    'traces.parquet', 'trace_index.parquet')},
              'schemas': {name: pq.ParquetFile(root / name).schema_arrow.names
                          for name in ('metrics.parquet', 'logs.parquet',
                                       'traces.parquet', 'trace_index.parquet')},
              'metric_names': dict(names.most_common()),
              'metric_label_keys': dict(Counter(key for row in labels for key in row).most_common()),
              'metric_services': dict(services.most_common(18)),
              'metric_pods': dict(pods.most_common(15)),
              'topology_keys': list(topology),
              'topology_snapshot_count': len(topology.get('snapshots', []))}
    return result


if __name__ == '__main__':
    print(json.dumps({case_id: inspect(case_id, root)
                      for case_id, root in SOURCES.items()}, indent=2))
