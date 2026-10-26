from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.storage import CaseStorage, StorageError


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def canonical_record(record: dict[str, Any]) -> dict[str, Any]:
    return {key: _json_value(value) for key, value in record.items() if not key.startswith("_")}


def record_digest(record: dict[str, Any]) -> str:
    raw = json.dumps(canonical_record(record), ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


class EvidenceVerifier:
    def __init__(self, storage: CaseStorage, demo_fixture: Path):
        self.storage = storage
        self.demo = json.loads(demo_fixture.read_text(encoding="utf-8"))
        self.demo_index: dict[tuple[str, str], dict[str, Any]] = {}
        for item in self.demo["records"]:
            full = canonical_record(item["record"])
            self.demo_index[(item["modality"], record_digest(full))] = full

    def case_window(self, case_id: str) -> tuple[datetime, datetime, dict[str, Any] | None]:
        if case_id == self.demo["case_id"]:
            return parse_utc(self.demo["start_time"]), parse_utc(self.demo["end_time"]), None
        manifest = self.storage.load_manifest(case_id, mock_only=False)
        return parse_utc(manifest["start_time"]), parse_utc(manifest["end_time"]), manifest

    async def validate_answer(self, answer: dict[str, Any]) -> dict[str, Any]:
        if answer.get("task_type") == "SEMIFINAL_QUERY_VISUALIZATION":
            return self._validate_qa_answer(answer)
        case_id = answer["case_id"]
        case_start, case_end, manifest = self.case_window(case_id)
        checks: list[dict[str, Any]] = []
        all_valid = True
        for event_index, event in enumerate(answer["anomalies"]):
            seen_evidence: set[tuple[str, str]] = set()
            event_start = parse_utc(event["start_time"])
            event_end = parse_utc(event["end_time"])
            if event_start < case_start or event_end > case_end:
                checks.append({"event": event_index, "valid": False, "code": "EVENT_OUTSIDE_CASE_WINDOW"})
                all_valid = False
            for evidence_index, evidence in enumerate(event["evidence"]):
                evidence_key = (evidence["modality"], evidence["record_sha256"])
                if evidence_key in seen_evidence:
                    checks.append({
                        "event": event_index,
                        "evidence": evidence_index,
                        "valid": False,
                        "code": "DUPLICATE_EVIDENCE",
                    })
                    all_valid = False
                    continue
                seen_evidence.add(evidence_key)
                check = await self._validate_one(case_id, manifest, evidence)
                check.update({"event": event_index, "evidence": evidence_index})
                checks.append(check)
                all_valid = all_valid and bool(check["valid"])
        return {"valid": all_valid, "checks": checks, "case_id": case_id}

    def _validate_qa_answer(self, answer: dict[str, Any]) -> dict[str, Any]:
        """Validate artifact/source integrity before private deterministic scoring.

        Numeric correctness is intentionally handled by the private grader
        against its frozen, independently generated rubric.
        """
        case_id = str(answer["case_id"])
        case_start, case_end, _ = self.case_window(case_id)
        checks: list[dict[str, Any]] = []
        valid = True
        source = answer.get("data_source", {})
        try:
            source_start = parse_utc(str(source["start_time"]))
            source_end = parse_utc(str(source["end_time"]))
            window_valid = case_start <= source_start < source_end <= case_end
        except (KeyError, ValueError):
            window_valid = False
        checks.append({"component": "data_source", "valid": window_valid, "code": "SOURCE_WINDOW_VALID" if window_valid else "SOURCE_WINDOW_INVALID"})
        valid = valid and window_valid

        point_count = 0
        points_valid = True
        try:
            bucket_seconds = int(answer.get("processing", {}).get("bucket_seconds", 0))
        except (TypeError, ValueError):
            bucket_seconds = 0
        bucket_seconds = min(max(bucket_seconds, 0), 3600)
        earliest_bucket_label = case_start - timedelta(seconds=bucket_seconds)
        try:
            for series in answer["chart"]["series"]:
                for point in series["points"]:
                    timestamp = parse_utc(str(point["timestamp"]))
                    # A fixed-width aggregate is conventionally labelled by
                    # its bucket start, which may precede a sub-minute case
                    # start by less than one declared bucket.
                    if not (earliest_bucket_label <= timestamp <= case_end):
                        points_valid = False
                    point_count += 1
        except (KeyError, TypeError, ValueError):
            points_valid = False
        points_valid = points_valid and point_count > 0
        checks.append({"component": "chart_data", "valid": points_valid, "code": "CHART_POINTS_VALID" if points_valid else "CHART_POINTS_INVALID", "point_count": point_count})
        valid = valid and points_valid

        artifact_valid = False
        try:
            artifact = answer["chart"]["artifact"]
            content = base64.b64decode(artifact["data_base64"], validate=True)
            artifact_valid = (
                content.startswith(b"\x89PNG\r\n\x1a\n")
                and len(content) <= 1_500_000
                and hashlib.sha256(content).hexdigest() == artifact["sha256"]
            )
        except (KeyError, TypeError, ValueError):
            artifact_valid = False
        checks.append({"component": "chart_artifact", "valid": artifact_valid, "code": "PNG_HASH_VERIFIED" if artifact_valid else "PNG_INVALID"})
        valid = valid and artifact_valid
        return {"valid": valid, "checks": checks, "case_id": case_id, "question_id": answer.get("question_id")}

    async def _validate_one(
        self, case_id: str, manifest: dict[str, Any] | None, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        digest = evidence["record_sha256"]
        submitted = canonical_record(evidence["record"])
        if case_id == self.demo["case_id"]:
            full = self.demo_index.get((evidence["modality"], digest))
            return self._compare(full, submitted, digest, synthetic=True)
        if manifest is None:
            return {"valid": False, "code": "CASE_NOT_AVAILABLE"}
        try:
            candidates = await self._public_candidates(manifest, evidence["modality"], submitted)
        except (KeyError, ValueError, StorageError) as exc:
            return {"valid": False, "code": "EVIDENCE_QUERY_INVALID", "detail": str(exc)[:200]}
        for candidate in candidates:
            if record_digest(candidate) == digest:
                return self._compare(canonical_record(candidate), submitted, digest)
        return {"valid": False, "code": "EVIDENCE_NOT_FOUND", "record_sha256": digest}

    @staticmethod
    def _compare(
        full: dict[str, Any] | None,
        submitted: dict[str, Any],
        digest: str,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        if full is None:
            return {"valid": False, "code": "EVIDENCE_NOT_FOUND", "record_sha256": digest}
        for key, value in submitted.items():
            if key not in full or _json_value(full[key]) != _json_value(value):
                return {"valid": False, "code": "EVIDENCE_RECORD_MISMATCH", "field": key}
        return {
            "valid": True,
            "code": "VERIFIED_SYNTHETIC_DEMO_RECORD" if synthetic else "VERIFIED_PUBLIC_RECORD",
            "record_sha256": digest,
            "verified_record": full,
        }

    async def _public_candidates(
        self, manifest: dict[str, Any], modality: str, record: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if modality == "metrics":
            timestamp = parse_utc(str(record["timestamp"]))
            return await self.storage.query_metrics(
                manifest, start=timestamp, end=timestamp + timedelta(microseconds=1),
                metric_name=record.get("metric_name"), service=record.get("service"),
                pod=record.get("pod"), node=record.get("node"), aggregation="raw",
                step_seconds=60, limit=100, offset=0,
            )
        if modality == "logs":
            timestamp = parse_utc(str(record["timestamp"]))
            return await self.storage.query_logs(
                manifest, start=timestamp, end=timestamp + timedelta(microseconds=1),
                service=record.get("service"), pod=record.get("pod"),
                severity=record.get("severity"), trace_id=record.get("trace_id"),
                keyword=None, limit=100, offset=0,
            )
        if modality == "traces":
            trace_id = str(record["trace_id"])
            return await self.storage.get_trace(manifest, trace_id)
        raise ValueError("unsupported evidence modality")
