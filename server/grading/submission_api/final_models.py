from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import AnomalyEventV3, ChartSubmission, Evidence


class FinalAnomaly(AnomalyEventV3):
    # A missing evidence list is scoreable as zero evidence, unlike a forged ref.
    evidence: list[Evidence] = Field(default_factory=list, max_length=20)


class FinalRootHypothesis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    root_entity: str = Field(min_length=1, max_length=256)
    root_cause_type: str = Field(min_length=1, max_length=128)
    propagation_path: list[str] = Field(default_factory=list, max_length=32)
    supporting_evidence: list[str] = Field(default_factory=list, max_length=32)


class FinalAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: Literal["FINAL_ANOMALY_INVESTIGATION"]
    schema_version: Literal["final-1.0"]
    task_id: str = Field(pattern=r"^(FT-0[1-3]|PK-R0[1-9])$")
    case_id: str = Field(pattern=r"^SF-\d{3}$")
    run_id: str = Field(min_length=1, max_length=128)
    agent_status: Literal["COMPLETED", "PARTIAL", "FAILED", "ABSTAINED"]
    anomalies: list[FinalAnomaly] = Field(default_factory=list, max_length=32)
    charts: list[ChartSubmission] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=8000)
    root_hypothesis: FinalRootHypothesis | None = None
    provider_reported_usage: dict[str, int] | None = None


class FinalSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    answer_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    answer: FinalAnswer

    @model_validator(mode="after")
    def check_times(self):
        for item in self.answer.anomalies:
            if item.start_time.tzinfo is None or item.end_time.tzinfo is None:
                raise ValueError("anomaly times require timezone")
        return self
