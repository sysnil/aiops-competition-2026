"""Read-only SF-005 checks through the API storage code as the API account."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import sys

sys.path.insert(0, '/opt/aiops-competition-visualization')
sys.path.insert(0, '/opt/aiops-contest-server')

from app.settings import load_settings
from app.storage import CaseStorage
from data import counts, edge_summary, load_case, metric_series, verify_files


async def main():
    store = CaseStorage(load_settings())
    manifest = store.load_manifest('SF-005', mock_only=False)
    start = datetime.fromisoformat(manifest['start_time'])
    end = datetime.fromisoformat(manifest['end_time'])
    if manifest['source_manifest_sha256'] != \
            '3bddfc396be4bb0a6002d1bbd959c44bf770a692e916dfd5c71da31fafd756f2':
        raise ValueError('SF-005 registered from a different Public source')
    metrics = await store.query_metrics(manifest, start=start, end=end,
        metric_name='container_memory_working_set_bytes', service=None, pod=None,
        node=None, aggregation='raw', step_seconds=60, limit=5, offset=0)
    error_logs = await store.query_logs(manifest, start=start, end=end,
        service='recommendation', pod=None, severity='ERROR', trace_id=None,
        keyword=None, limit=100, offset=0)
    recommendation = await store.query_traces(manifest, start=start, end=end,
        service='recommendation', status=None, operation_name=None,
        parent_service=None, limit=5, offset=0)
    recommendation_count = await store._query(
        'SELECT count(*) AS n FROM read_parquet(?) WHERE service = ? '
        'AND start_time >= ? AND start_time < ? '
        'AND json_extract_string(attributes, \'$."span.kind"\') = ?',
        [str(store._case_dir(manifest) / 'traces.parquet'),
         'recommendation', start, end, 'server'])
    edge_errors = await store.query_traces(manifest, start=start, end=end,
        service='product-catalog', status='ERROR', operation_name=None,
        parent_service='frontend', limit=100, offset=0)
    sample_trace = manifest['sample_trace_id']
    waterfall = await store.get_trace(manifest, sample_trace)
    case = load_case('SF-005')
    if case is None:
        raise ValueError('Organizer Public loader cannot see SF-005')
    observed = counts(case, case['start'], case['end'])
    hashes = verify_files(case)
    curve = metric_series(case, case['start'], case['end'],
        'container_memory_working_set_bytes', 'frontend', None)
    edge = edge_summary(case, case['start'], case['end'],
                        None, ('frontend', 'product-catalog'))
    if (not metrics or not error_logs or not recommendation or not recommendation_count or
            recommendation_count[0]['n'] <= 0 or not edge_errors or
            not waterfall or curve.empty or edge.empty or
            not all(row['match'] and row['size_match'] for row in hashes.values()) or
            observed != {'metrics': 38425, 'logs': 281046,
                         'traces': 420364, 'trace_index': 60102}):
        raise ValueError('SF-005 API/organizer Public readback incomplete')
    result = {'status': 'SF005_PUBLIC_BACKEND_QUERY_PASS',
        'source_manifest_sha256': manifest['source_manifest_sha256'],
        'case_window': [manifest['start_time'], manifest['end_time']],
        'normalized_row_counts': observed,
        'storage_metric_rows_returned': len(metrics),
        'storage_recommendation_error_logs': len(error_logs),
        'storage_recommendation_trace_rows_returned': len(recommendation),
        'storage_recommendation_server_span_count': recommendation_count[0]['n'],
        'storage_frontend_product_catalog_error_spans': len(edge_errors),
        'storage_sample_trace_span_count': len(waterfall),
        'organizer_frontend_metric_minutes': len(curve),
        'organizer_frontend_product_catalog_edge_rows': len(edge),
        'normalized_file_sha256_checks': hashes,
        'private_gt_read': False, 'team_http_route_tested': False}
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    asyncio.run(main())
