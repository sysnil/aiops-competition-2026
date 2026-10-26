from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from .settings import Settings


CASE_ID_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
TRACE_ID_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
MEMORY_RE = re.compile(r"^[1-9][0-9]*(?:MB|GB)$")


class StorageError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class QueryTimeout(StorageError):
    def __init__(self):
        super().__init__("QUERY_TIMEOUT", "query exceeded configured timeout", 504)


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _json_fields(rows: list[dict[str, Any]], fields: Iterable[str]) -> list[dict[str, Any]]:
    for row in rows:
        for field in fields:
            value = row.get(field)
            if isinstance(value, str):
                try:
                    row[field] = json.loads(value)
                except json.JSONDecodeError:
                    pass
    return rows


class CaseStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._global_slots = asyncio.Semaphore(settings.total_query_concurrency)
        if not MEMORY_RE.fullmatch(settings.duckdb_memory_limit):
            raise ValueError("unsafe DuckDB memory_limit setting")

    def _manifest_paths(self) -> list[Path]:
        paths: list[Path] = []
        for root in (self.settings.public_root, self.settings.mock_root):
            manifest_root = root / "manifests"
            if manifest_root.is_dir():
                paths.extend(sorted(manifest_root.glob("*.json")))
        return paths

    def list_cases(self, *, mock_only: bool) -> list[dict[str, Any]]:
        cases = []
        for path in self._manifest_paths():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("publication_status") not in {"published", "mock_published"}:
                continue
            if mock_only and manifest.get("classification") != "MOCK_ONLY":
                continue
            if not mock_only and manifest.get("classification") == "MOCK_ONLY":
                continue
            cases.append(
                {
                    "case_id": manifest["case_id"],
                    "dataset_schema_version": manifest["dataset_schema_version"],
                    "start_time": manifest["start_time"],
                    "end_time": manifest["end_time"],
                    "available_modalities": manifest["available_modalities"],
                    "classification": manifest["classification"],
                }
            )
        return sorted(cases, key=lambda item: item["case_id"])

    def load_manifest(self, case_id: str, *, mock_only: bool) -> dict[str, Any]:
        if not CASE_ID_RE.fullmatch(case_id):
            raise StorageError("INVALID_CASE_ID", "case_id format is invalid", 400)
        root = self.settings.mock_root if mock_only else self.settings.public_root
        candidates = [root / "manifests" / f"{case_id}.json"]
        for path in candidates:
            if not path.is_file():
                continue
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("publication_status") not in {"published", "mock_published"}:
                break
            if mock_only and manifest.get("classification") != "MOCK_ONLY":
                break
            return manifest
        raise StorageError("CASE_NOT_FOUND", "case is not published or not authorized", 404)

    def public_metadata(self, manifest: dict[str, Any]) -> dict[str, Any]:
        allowed = (
            "case_id",
            "dataset_schema_version",
            "start_time",
            "end_time",
            "available_modalities",
            "public_services",
            "public_topology",
            "data_source",
            "immutable_manifest_hash",
            "classification",
        )
        return {key: manifest[key] for key in allowed if key in manifest}

    def _case_dir(self, manifest: dict[str, Any]) -> Path:
        root = self.settings.mock_root if manifest["classification"] == "MOCK_ONLY" else self.settings.public_root
        directory = (root / "cases" / manifest["case_id"] / manifest["version"]).resolve()
        allowed_root = (root / "cases").resolve()
        if not directory.is_relative_to(allowed_root) or not directory.is_dir():
            raise StorageError("CASE_STORAGE_ERROR", "published case storage is unavailable", 503)
        return directory

    def validate_window(
        self,
        manifest: dict[str, Any],
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[datetime, datetime]:
        case_start = _parse_utc(manifest["start_time"])
        case_end = _parse_utc(manifest["end_time"])
        query_start = (start or case_start).astimezone(timezone.utc)
        query_end = (end or case_end).astimezone(timezone.utc)
        if query_start >= query_end:
            raise StorageError("INVALID_TIME_RANGE", "query start must be before query end", 400)
        if (query_end - query_start).total_seconds() > self.settings.max_time_range_seconds:
            raise StorageError("TIME_RANGE_TOO_LARGE", "query time range exceeds configured maximum", 400)
        return query_start, query_end

    def validate_page(self, page: int, page_size: int) -> tuple[int, int]:
        if page < 1 or page > self.settings.max_page:
            raise StorageError("INVALID_PAGE", "page is outside the allowed range", 400)
        if page_size < 1 or page_size > self.settings.max_rows:
            raise StorageError("INVALID_PAGE_SIZE", "page_size is outside the allowed range", 400)
        return page_size, (page - 1) * page_size

    def _connection(self) -> duckdb.DuckDBPyConnection:
        connection = duckdb.connect(":memory:")
        connection.execute(f"SET threads = {self.settings.duckdb_threads}")
        connection.execute(f"SET memory_limit = '{self.settings.duckdb_memory_limit}'")
        escaped = str(self.settings.duckdb_temp_directory).replace("'", "''")
        connection.execute(f"SET temp_directory = '{escaped}'")
        return connection

    async def _query(self, sql: str, params: list[Any]) -> list[dict[str, Any]]:
        connection = self._connection()

        def execute() -> list[dict[str, Any]]:
            try:
                return connection.execute(sql, params).fetch_arrow_table().to_pylist()
            finally:
                connection.close()

        async with self._global_slots:
            task = asyncio.create_task(asyncio.to_thread(execute))
            try:
                return await asyncio.wait_for(asyncio.shield(task), self.settings.query_timeout_seconds)
            except TimeoutError as exc:
                connection.interrupt()
                await asyncio.gather(task, return_exceptions=True)
                raise QueryTimeout() from exc

    @staticmethod
    def _filters(mapping: list[tuple[str, Any]]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        for field, value in mapping:
            if value is not None:
                clauses.append(f"{field} = ?")
                params.append(value)
        return (" AND ".join(clauses), params)

    async def query_metrics(
        self,
        manifest: dict[str, Any],
        *,
        start: datetime,
        end: datetime,
        metric_name: str | None,
        service: str | None,
        pod: str | None,
        node: str | None,
        aggregation: str,
        step_seconds: int,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        path = self._case_dir(manifest) / "metrics.parquet"
        filters, params = self._filters(
            [("metric_name", metric_name), ("service", service), ("pod", pod), ("node", node)]
        )
        where = "timestamp >= ? AND timestamp < ?"
        values: list[Any] = [str(path), start, end]
        if filters:
            where += " AND " + filters
            values.extend(params)
        if aggregation == "raw":
            sql = f"""
                SELECT timestamp, metric_name, value, labels, service, pod, node,
                       unit, metric_type, source
                FROM read_parquet(?) WHERE {where}
                ORDER BY timestamp, metric_name, service, pod, node, value
                LIMIT ? OFFSET ?
            """
        else:
            function = {"avg": "avg", "min": "min", "max": "max", "sum": "sum"}.get(aggregation)
            if function is None:
                raise StorageError("INVALID_AGGREGATION", "unsupported aggregation", 400)
            sql = f"""
                SELECT to_timestamp(floor(epoch(timestamp) / ?) * ?) AS timestamp,
                       metric_name, {function}(value) AS value, service, pod, node,
                       any_value(unit) AS unit, any_value(metric_type) AS metric_type,
                       '{aggregation}' AS aggregation
                FROM read_parquet(?) WHERE {where}
                GROUP BY 1, 2, 4, 5, 6
                ORDER BY timestamp, metric_name, service, pod, node
                LIMIT ? OFFSET ?
            """
            values = [step_seconds, step_seconds] + values
        values.extend([limit, offset])
        return _json_fields(await self._query(sql, values), ("labels",))

    async def query_logs(
        self,
        manifest: dict[str, Any],
        *,
        start: datetime,
        end: datetime,
        service: str | None,
        pod: str | None,
        severity: str | None,
        trace_id: str | None,
        keyword: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        path = self._case_dir(manifest) / "logs.parquet"
        filters, extra = self._filters(
            [("service", service), ("pod", pod), ("severity", severity), ("trace_id", trace_id)]
        )
        where = "timestamp >= ? AND timestamp < ?"
        params: list[Any] = [str(path), start, end]
        if filters:
            where += " AND " + filters
            params.extend(extra)
        if keyword:
            where += " AND contains(lower(body), lower(?))"
            params.append(keyword)
        sql = f"""
            SELECT timestamp, service, pod, severity, body, attributes, trace_id, span_id
            FROM read_parquet(?) WHERE {where}
            ORDER BY timestamp, service, pod, severity, body, trace_id, span_id
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        return _json_fields(await self._query(sql, params), ("attributes",))

    async def query_traces(
        self,
        manifest: dict[str, Any],
        *,
        start: datetime,
        end: datetime,
        service: str | None,
        status: str | None,
        operation_name: str | None,
        parent_service: str | None,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        path = self._case_dir(manifest) / "traces.parquet"
        filters, extra = self._filters(
            [
                ("service", service),
                ("status", status),
                ("operation_name", operation_name),
                ("parent_service", parent_service),
            ]
        )
        where = "start_time >= ? AND start_time < ?"
        params: list[Any] = [str(path), start, end]
        if filters:
            where += " AND " + filters
            params.extend(extra)
        sql = f"""
            SELECT trace_id, span_id, parent_span_id, parent_service, service,
                   operation_name, start_time, duration_ms, status,
                   attributes, resource_attributes
            FROM read_parquet(?) WHERE {where}
            ORDER BY start_time, trace_id, span_id
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        return _json_fields(await self._query(sql, params), ("attributes", "resource_attributes"))

    async def get_trace(self, manifest: dict[str, Any], trace_id: str) -> list[dict[str, Any]]:
        if not TRACE_ID_RE.fullmatch(trace_id):
            raise StorageError("INVALID_TRACE_ID", "trace_id format is invalid", 400)
        case_dir = self._case_dir(manifest)
        path = case_dir / "traces.parquet"
        index_path = case_dir / "trace_index.parquet"
        if index_path.is_file():
            index_rows = await self._query(
                "SELECT span_count FROM read_parquet(?) WHERE trace_id = ? LIMIT 1",
                [str(index_path), trace_id],
            )
            if not index_rows:
                raise StorageError("TRACE_NOT_FOUND", "trace_id was not found in this case", 404)
        sql = """
            SELECT trace_id, span_id, parent_span_id, parent_service, service,
                   operation_name, start_time, duration_ms, status,
                   attributes, resource_attributes
            FROM read_parquet(?) WHERE trace_id = ?
            ORDER BY start_time, span_id LIMIT ?
        """
        rows = await self._query(sql, [str(path), trace_id, self.settings.max_trace_spans])
        if not rows:
            raise StorageError("TRACE_NOT_FOUND", "trace_id was not found in this case", 404)
        return _json_fields(rows, ("attributes", "resource_attributes"))
