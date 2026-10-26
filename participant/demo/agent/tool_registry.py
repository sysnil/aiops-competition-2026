from __future__ import annotations

from typing import Any, Callable

from client.public_api import APIResult, PublicAPI


class ToolRegistry:
    """Stable tool names isolate the agent from HTTP route details."""

    def __init__(self, api: PublicAPI):
        self.api = api
        self._tools: dict[str, Callable[..., Any]] = {
            "list_cases": self.list_cases,
            "get_case_metadata": self.get_case_metadata,
            "get_topology": self.get_topology,
            "get_fault_taxonomy": self.get_fault_taxonomy,
            "get_anomaly_taxonomy": self.get_anomaly_taxonomy,
            "query_metrics": self.query_metrics,
            "query_logs": self.query_logs,
            "query_traces": self.query_traces,
            "get_trace": self.get_trace,
        }

    def names(self) -> list[str]:
        return sorted(self._tools)

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self._tools:
            raise LookupError(f"unknown tool: {name}")
        return self._tools[name](**kwargs)

    def list_cases(self) -> APIResult:
        return self.api.get(self.api.competition_path("cases"))

    def get_case_metadata(self, case_id: str) -> APIResult:
        return self.api.get(self.api.competition_path(f"cases/{case_id}/metadata"))

    def get_topology(self, case_id: str) -> APIResult:
        return self.api.get(self.api.competition_path(f"cases/{case_id}/topology"))

    def get_fault_taxonomy(self) -> APIResult:
        return self.api.get("/v1/fault-taxonomy")

    def get_anomaly_taxonomy(self) -> APIResult:
        return self.api.get("/v1/anomaly-taxonomy")

    def query_metrics(self, case_id: str, filters: dict[str, Any], page_size: int = 500, max_pages: int = 1) -> list[dict[str, Any]]:
        return self.api.paged(self.api.competition_path(f"cases/{case_id}/metrics"), filters, max_pages=max_pages, page_size=page_size)

    def query_logs(self, case_id: str, filters: dict[str, Any], page_size: int = 500, max_pages: int = 1) -> list[dict[str, Any]]:
        return self.api.paged(self.api.competition_path(f"cases/{case_id}/logs"), filters, max_pages=max_pages, page_size=page_size)

    def query_traces(self, case_id: str, filters: dict[str, Any], page_size: int = 500, max_pages: int = 1) -> list[dict[str, Any]]:
        return self.api.paged(self.api.competition_path(f"cases/{case_id}/traces"), filters, max_pages=max_pages, page_size=page_size)

    def get_trace(self, case_id: str, trace_id: str) -> APIResult:
        return self.api.get(self.api.competition_path(f"cases/{case_id}/traces/{trace_id}"))
