from __future__ import annotations

import base64
import hashlib
import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


FAULT_TYPES = {
    "ServicePause", "ServiceCrash", "PodCPUThrottle", "PodMemoryPressure",
    "PodRestart", "NodeCPUContention", "NodeMemoryPressure", "NetworkDelay",
    "NetworkPacketLoss", "NetworkBandwidthLimit", "DNSError", "DatabaseLatency",
    "DependencyFailure", "ConfigurationError", "UNKNOWN",
}
ENTITY_TYPES = {"Service", "Pod", "Node", "Network", "MetricSeries", "Unknown"}
ANOMALY_TYPES = {
    "REQUEST_LATENCY_INCREASE", "REQUEST_ERROR_INCREASE", "REQUEST_TIMEOUT_INCREASE",
    "CPU_USAGE_INCREASE", "MEMORY_USAGE_INCREASE", "POD_RESTART_INCREASE",
    "LOG_ERROR_BURST", "TRACE_LATENCY_INCREASE", "CALL_EDGE_LATENCY_INCREASE",
    "CALL_EDGE_ERROR_INCREASE", "OTHER_OBSERVABLE_ANOMALY",
}
ANOMALY_ENTITY_TYPES = ENTITY_TYPES | {"CallEdge"}


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["metrics", "logs", "traces"]
    request_id: str | None = Field(default=None, max_length=128)
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record: dict[str, Any]

    @field_validator("record")
    @classmethod
    def validate_record(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value or len(value) > 24:
            raise ValueError("evidence record must contain 1 to 24 fields")
        forbidden = {"path", "file", "filename", "sql", "query", "command", "cmd"}
        if any(str(key).lower() in forbidden for key in value):
            raise ValueError("evidence record contains a forbidden field")
        encoded = str(value)
        if len(encoded.encode("utf-8")) > 16_384:
            raise ValueError("evidence record is too large")
        return value


class FaultEventV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_type: str
    entity_id: str = Field(min_length=1, max_length=256)
    suspected_fault_type: str
    start_time: datetime
    end_time: datetime
    evidence: list[Evidence] = Field(min_length=1, max_length=20)

    @field_validator("entity_type")
    @classmethod
    def entity_type_is_public(cls, value: str) -> str:
        if value not in ENTITY_TYPES:
            raise ValueError("unsupported entity_type")
        return value

    @field_validator("entity_id")
    @classmethod
    def entity_is_not_a_path(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("entity_id must not contain a path")
        return value

    @field_validator("suspected_fault_type")
    @classmethod
    def fault_type_is_public(cls, value: str) -> str:
        if value not in FAULT_TYPES:
            raise ValueError("unsupported suspected_fault_type")
        return value

    @model_validator(mode="after")
    def ordered_time(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("times must include timezone")
        if self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
        return self


class AnswerV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
    schema_version: Literal["2.0"]
    anomalies: list[FaultEventV2] = Field(max_length=10)


class AnomalyEventV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anomaly_type: str
    anomaly_description: str = Field(min_length=8, max_length=1000)
    entity_type: str
    entity_id: str = Field(min_length=1, max_length=256)
    start_time: datetime
    end_time: datetime
    evidence: list[Evidence] = Field(min_length=1, max_length=20)

    @field_validator("anomaly_type")
    @classmethod
    def anomaly_type_is_public(cls, value: str) -> str:
        if value not in ANOMALY_TYPES:
            raise ValueError("unsupported anomaly_type")
        return value

    @field_validator("entity_type")
    @classmethod
    def entity_type_is_public(cls, value: str) -> str:
        if value not in ANOMALY_ENTITY_TYPES:
            raise ValueError("unsupported entity_type")
        return value

    @field_validator("entity_id")
    @classmethod
    def entity_is_not_a_path(cls, value: str) -> str:
        if "/" in value or "\\" in value or ".." in value:
            raise ValueError("entity_id must not contain a path")
        return value

    @model_validator(mode="after")
    def ordered_time(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("times must include timezone")
        if self.start_time > self.end_time:
            raise ValueError("start_time must not be after end_time")
        return self


class AnswerV3(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
    schema_version: Literal["3.0"]
    task_type: Literal["SEMIFINAL_ANOMALY_DETECTION"]
    # Keep this aligned with anomaly-gt-v1.schema.json.  Sixteen covers the
    # current 13-event draft without weakening per-event or body-size limits.
    anomalies: list[AnomalyEventV3] = Field(max_length=16)


class ChartPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: datetime
    value: float | None

    @field_validator("value")
    @classmethod
    def finite_value(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("chart point must be finite")
        return value


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=256)
    unit: str = Field(min_length=1, max_length=32)
    points: list[ChartPoint] = Field(min_length=1, max_length=3600)


class ChartAxis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=128)
    unit: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(default=None, max_length=32)


class ChartArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    media_type: Literal["image/png"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_base64: str = Field(min_length=16, max_length=2_000_000)

    @model_validator(mode="after")
    def valid_png(self):
        try:
            content = base64.b64decode(self.data_base64, validate=True)
        except ValueError as exc:
            raise ValueError("chart artifact is not valid base64") from exc
        if len(content) > 1_500_000 or not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("chart artifact must be a bounded PNG image")
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("chart artifact sha256 mismatch")
        return self


class ChartSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chart_type: Literal["line"]
    title: str = Field(min_length=1, max_length=256)
    x_axis: ChartAxis
    y_axis: ChartAxis
    series: list[ChartSeries] = Field(min_length=1, max_length=4)
    artifact: ChartArtifact


class QuestionAnswerItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_index: int = Field(ge=1, le=3)
    answer_type: str = Field(min_length=1, max_length=64)
    value: float | int | str | bool
    unit: str | None = Field(default=None, max_length=32)
    timestamp: datetime | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    explanation: str = Field(min_length=1, max_length=1000)


class QueryDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["metrics", "logs", "traces"]
    start_time: datetime
    end_time: datetime
    filters: dict[str, Any]
    request_ids: list[str] = Field(min_length=1, max_length=200)
    record_count: int = Field(ge=1, le=2_000_000)

    @model_validator(mode="after")
    def ordered_time(self):
        if self.start_time.tzinfo is None or self.end_time.tzinfo is None:
            raise ValueError("data source times must include timezone")
        if self.start_time >= self.end_time:
            raise ValueError("data source start_time must be before end_time")
        return self


class QAAnswerV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["SEMIFINAL_QUERY_VISUALIZATION"]
    schema_version: Literal["1.0"]
    question_id: str = Field(pattern=r"^[A-Z][A-Z0-9_-]{1,31}$")
    case_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
    chart: ChartSubmission
    answers: list[QuestionAnswerItem] = Field(min_length=3, max_length=3)
    data_source: QueryDataSource
    processing: dict[str, Any]

    @model_validator(mode="after")
    def exactly_three_answers(self):
        if sorted(item.question_index for item in self.answers) != [1, 2, 3]:
            raise ValueError("answers must contain question_index 1, 2 and 3 exactly once")
        return self


class SubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{2,63}$")
    schema_version: Literal["1.0", "2.0", "3.0"]
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer: QAAnswerV1 | AnswerV2 | AnswerV3

    @model_validator(mode="after")
    def consistent_envelope(self):
        if self.case_id != self.answer.case_id or self.schema_version != self.answer.schema_version:
            raise ValueError("submission envelope does not match answer")
        return self
