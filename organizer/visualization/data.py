"""Read-only queries for locally imported, published competition Public bundles."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import duckdb
import pandas as pd


PUBLIC_ROOT = Path('/data/aiops-contest/public')
CASE_IDS = tuple(f'SF-{index:03d}' for index in range(1, 18))
FILES = ('metrics.parquet', 'logs.parquet', 'traces.parquet', 'trace_index.parquet')
TRACE_ID = re.compile(r'^[0-9a-fA-F]{16,64}$')


def load_case(case_id: str, root: Path = PUBLIC_ROOT) -> dict | None:
    if case_id not in CASE_IDS:
        raise ValueError('Unknown case')
    path = root / 'manifests' / f'{case_id}.json'
    if not path.is_file() or path.is_symlink():
        return None
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if (manifest.get('case_id') != case_id or
            manifest.get('publication_status') != 'published' or
            manifest.get('classification') != 'PUBLIC_COMPETITION_DATA'):
        return None
    version = manifest.get('version')
    if not isinstance(version, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', version):
        raise ValueError('Invalid Public bundle version')
    case_dir = root / 'cases' / case_id / version
    resolved = case_dir.resolve()
    if not resolved.is_relative_to((root / 'cases').resolve()) or not resolved.is_dir():
        return None
    entries = {row['name']: row for row in manifest.get('files', [])
               if isinstance(row, dict) and row.get('name') in FILES}
    if set(entries) != set(FILES) or any(not (resolved / name).is_file()
                                         or (resolved / name).is_symlink() for name in FILES):
        return None
    start = datetime.fromisoformat(manifest['start_time'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(manifest['end_time'].replace('Z', '+00:00'))
    if start.tzinfo is None or end.tzinfo is None or not 0 < (end - start).total_seconds() <= 3600:
        raise ValueError('Invalid published observation window')
    return {'id': case_id, 'manifest': manifest, 'manifest_path': path,
            'directory': resolved, 'start': start.astimezone(timezone.utc),
            'end': end.astimezone(timezone.utc), 'files': entries}


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(case: dict) -> dict:
    result = {}
    for name, info in case['files'].items():
        path = case['directory'] / name
        actual = checksum(path)
        result[name] = {'expected': info['sha256'], 'actual': actual,
                        'match': actual == info['sha256'],
                        'size_match': path.stat().st_size == info['size_bytes']}
    return result


def _query(sql: str, parameters: list, *, max_rows: int = 5000) -> pd.DataFrame:
    connection = duckdb.connect(':memory:')
    try:
        connection.execute('SET threads=2')
        connection.execute("SET memory_limit='768MB'")
        frame = connection.execute(sql, parameters).fetch_df()
        if len(frame) > max_rows:
            raise ValueError('Query exceeded visualization row limit')
        return frame
    finally:
        connection.close()


def _span_filter(service: str | None, pod: str | None, edge: tuple[str, str] | None,
                 *, alias: str = 't') -> tuple[str, list]:
    terms, values = [], []
    if service:
        terms.append(f'{alias}.service = ?')
        values.append(service)
    if pod:
        terms.append(f'json_extract_string({alias}.resource_attributes, \'$."k8s.pod.name"\') = ?')
        values.append(pod)
    if edge:
        terms.extend((f'{alias}.parent_service = ?', f'{alias}.service = ?'))
        values.extend(edge)
    return (' AND '.join(terms), values)


def facets(case: dict) -> dict:
    metric = str(case['directory'] / 'metrics.parquet')
    rows = _query("SELECT metric_name, count(*) n FROM read_parquet(?) "
                  "GROUP BY metric_name ORDER BY n DESC LIMIT 250", [metric], max_rows=250)
    services = sorted({name for name in case['manifest'].get('public_services', [])
                       if isinstance(name, str)})
    snapshots = case['manifest'].get('public_topology', {}).get('snapshots', [])
    pods = sorted({p['name'] for snapshot in snapshots for p in snapshot.get('pods', [])
                   if p.get('name')})
    edges = case['manifest'].get('public_topology', {}).get('observed_service_edges', [])
    pairs = sorted({(e['source'], e['destination']) for e in edges
                    if e.get('source') and e.get('destination')})
    return {'metrics': rows['metric_name'].tolist(), 'services': services,
            'pods': pods, 'edges': pairs}


def _service_pods(case: dict, services: tuple[str, ...]) -> list[str]:
    return sorted({pod['name'] for snapshot in case['manifest'].get('public_topology',
        {}).get('snapshots', []) for pod in snapshot.get('pods', [])
        if pod.get('service') in services and pod.get('name')})


def metric_series(case: dict, start: datetime, end: datetime, metric: str,
                  service: str | None, pod: str | None,
                  edge: tuple[str, str] | None = None, *, limit: int = 3000) -> pd.DataFrame:
    path = str(case['directory'] / 'metrics.parquet')
    sql = """SELECT date_trunc('minute', timestamp) AS minute, metric_name,
                    coalesce(service, 'UNATTRIBUTED') AS service,
                    coalesce(pod, 'UNATTRIBUTED') AS pod,
                    avg(value) AS value, count(*) AS samples,
                    any_value(unit) AS unit, any_value(metric_type) AS metric_type
             FROM read_parquet(?) WHERE timestamp >= ? AND timestamp < ? AND metric_name = ?"""
    params: list = [path, start, end, metric]
    if service:
        mapped = _service_pods(case, (service,))
        sql += ' AND (service = ?'
        params.append(service)
        if mapped:
            sql += ' OR pod IN (' + ','.join('?' for _ in mapped) + ')'
            params.extend(mapped)
        sql += ')'
    if pod:
        sql += ' AND pod = ?'
        params.append(pod)
    if edge and not service and not pod:
        mapped = _service_pods(case, edge)
        sql += ' AND (service IN (?, ?)'
        params.extend(edge)
        if mapped:
            sql += ' OR pod IN (' + ','.join('?' for _ in mapped) + ')'
            params.extend(mapped)
        sql += ')'
    sql += ' GROUP BY 1,2,3,4 ORDER BY 1,3,4 LIMIT ?'
    params.append(limit)
    return _query(sql, params, max_rows=limit)


def metric_inventory(case: dict, start: datetime, end: datetime) -> pd.DataFrame:
    path = str(case['directory'] / 'metrics.parquet')
    return _query('SELECT metric_name, count(*) AS samples, count(DISTINCT service) AS services '
                  'FROM read_parquet(?) WHERE timestamp >= ? AND timestamp < ? '
                  'GROUP BY 1 ORDER BY samples DESC LIMIT 120', [path, start, end], max_rows=120)


def log_summary(case: dict, start: datetime, end: datetime,
                service: str | None, pod: str | None,
                edge: tuple[str, str] | None = None) -> pd.DataFrame:
    path = str(case['directory'] / 'logs.parquet')
    sql = """SELECT date_trunc('minute', timestamp) AS minute,
                    CASE WHEN upper(severity) IN ('INFO','WARN','WARNING','ERROR','DEBUG','FATAL')
                         THEN upper(severity) ELSE 'UNKNOWN' END AS severity,
                    count(*) AS records FROM read_parquet(?)
             WHERE timestamp >= ? AND timestamp < ?"""
    params: list = [path, start, end]
    if service: sql += ' AND service = ?'; params.append(service)
    if pod: sql += ' AND pod = ?'; params.append(pod)
    if edge and not service and not pod:
        sql += ' AND service IN (?, ?)'; params.extend(edge)
    sql += ' GROUP BY 1,2 ORDER BY 1,2 LIMIT 3000'
    return _query(sql, params, max_rows=3000)


def logs(case: dict, start: datetime, end: datetime, service: str | None,
         pod: str | None, keyword: str, edge: tuple[str, str] | None = None,
         *, limit: int = 300, offset: int = 0) -> pd.DataFrame:
    path = str(case['directory'] / 'logs.parquet')
    sql = """SELECT timestamp, service, pod, coalesce(nullif(severity,''),'UNKNOWN') severity,
                    body, trace_id, span_id FROM read_parquet(?)
             WHERE timestamp >= ? AND timestamp < ?"""
    params: list = [path, start, end]
    if service: sql += ' AND service = ?'; params.append(service)
    if pod: sql += ' AND pod = ?'; params.append(pod)
    if edge and not service and not pod:
        sql += ' AND service IN (?, ?)'; params.extend(edge)
    if keyword: sql += ' AND contains(lower(body), lower(?))'; params.append(keyword[:200])
    sql += ' ORDER BY timestamp DESC LIMIT ? OFFSET ?'
    params += [limit, offset]
    return _query(sql, params, max_rows=limit)


def trace_list(case: dict, start: datetime, end: datetime, service: str | None,
               pod: str | None, edge: tuple[str, str] | None, *, limit: int = 120) -> pd.DataFrame:
    index = str(case['directory'] / 'trace_index.parquet')
    trace = str(case['directory'] / 'traces.parquet')
    sql = """SELECT i.trace_id, i.start_time, i.span_count
             FROM read_parquet(?) i WHERE i.start_time >= ? AND i.start_time < ?"""
    params: list = [index, start, end]
    filters, values = _span_filter(service, pod, edge)
    if filters:
        sql += f' AND EXISTS (SELECT 1 FROM read_parquet(?) t WHERE t.trace_id=i.trace_id AND {filters})'
        params += [trace, *values]
    sql += ' ORDER BY i.start_time DESC LIMIT ?'
    params.append(limit)
    return _query(sql, params, max_rows=limit)


def trace_spans(case: dict, trace_id: str, *, limit: int = 5000) -> pd.DataFrame:
    if not TRACE_ID.fullmatch(trace_id):
        raise ValueError('Invalid Trace ID')
    index = str(case['directory'] / 'trace_index.parquet')
    trace = str(case['directory'] / 'traces.parquet')
    exists = _query('SELECT span_count FROM read_parquet(?) WHERE trace_id = ? LIMIT 1',
                    [index, trace_id], max_rows=1)
    if exists.empty:
        return pd.DataFrame()
    return _query('SELECT trace_id, span_id, parent_span_id, parent_service, service, '
                  'operation_name, start_time, duration_ms, status, attributes, '
                  'resource_attributes FROM read_parquet(?) WHERE trace_id = ? '
                  'ORDER BY start_time, span_id LIMIT ?', [trace, trace_id, limit], max_rows=limit)


def edge_summary(case: dict, start: datetime, end: datetime,
                 service: str | None, edge: tuple[str, str] | None) -> pd.DataFrame:
    path = str(case['directory'] / 'traces.parquet')
    sql = """SELECT parent_service AS "source", service AS "destination",
                    count(*) calls,
                    quantile_cont(duration_ms, 0.95) p95_ms,
                    sum(CASE WHEN upper(coalesce(status,'')) IN ('ERROR','STATUS_CODE_ERROR')
                             THEN 1 ELSE 0 END) errors
             FROM read_parquet(?) WHERE start_time >= ? AND start_time < ?
             AND parent_service IS NOT NULL AND parent_service <> service"""
    params: list = [path, start, end]
    if service: sql += ' AND (parent_service = ? OR service = ?)'; params += [service, service]
    if edge: sql += ' AND parent_service = ? AND service = ?'; params += list(edge)
    sql += ' GROUP BY 1,2 ORDER BY calls DESC LIMIT 150'
    return _query(sql, params, max_rows=150)


def counts(case: dict, start: datetime, end: datetime) -> dict:
    result = {}
    for key, time_col in (('metrics', 'timestamp'), ('logs', 'timestamp'),
                          ('traces', 'start_time'), ('trace_index', 'start_time')):
        result[key] = int(_query(f'SELECT count(*) n FROM read_parquet(?) WHERE {time_col} >= ? '
                                  f'AND {time_col} < ?', [str(case['directory'] /
                                  f'{key}.parquet'), start, end], max_rows=1).iloc[0]['n'])
    return result
