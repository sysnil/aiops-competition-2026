"""Small, independent extension points for the semifinal Starter.

Every function receives only public Question/API data.  Unfinished functions
raise ``TodoNotImplemented`` so the caller can emit an honest, schema-valid
``NOT_IMPLEMENTED`` answer instead of inventing a value.
"""

from __future__ import annotations

from typing import Any


class TodoNotImplemented(NotImplementedError):
    pass


def chart_axes(public_chart: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """TODO-01: return exact axis metadata from the public chart requirement.

    The starter intentionally uses readable generic labels.  Keep the UTC
    timezone and public unit; do not hard-code a formal question or GT value.
    """
    return (
        {"label": "Time", "timezone": "UTC"},
        {"label": "Value", "unit": str(public_chart["y_axis"])},
    )


def cpu_answer(answer_type: str, points: list[dict[str, Any]], unit: str) -> dict[str, Any]:
    """TODO-02: answer max value, earliest max time, and mean for CPU rate.

    ``points`` already contains the starter's basic rate series.  Before using
    it for answers, inspect the query code and make Counter grouping/reset
    handling robust for arbitrary entities and Labels.
    """
    raise TodoNotImplemented("TODO-02")


def compare_series_answer(
    answer_type: str,
    series: list[dict[str, Any]],
    unit: str,
) -> dict[str, Any]:
    """TODO-03: compare aligned public time series without assuming names."""
    raise TodoNotImplemented("TODO-03")


def p95_window_answer(
    points: list[dict[str, Any]],
    minute_ranges: list[list[int]],
    unit: str,
) -> dict[str, Any]:
    """TODO-04: compare two p95 windows while ignoring null buckets."""
    raise TodoNotImplemented("TODO-04")


def edge_window_answer(
    points: list[dict[str, Any]],
    minute_ranges: list[list[int]],
    aggregation: str,
    unit: str,
) -> dict[str, Any]:
    """TODO-05: compare two validated call-edge windows."""
    raise TodoNotImplemented("TODO-05")


def select_call_edge_spans(
    spans: list[dict[str, Any]],
    parent_service: str,
    child_service: str,
) -> list[dict[str, Any]]:
    """TODO-05: return real child Server Spans for one service call edge.

    Build an index by ``(trace_id, span_id)``.  A selected row must be a
    Server Span owned by ``child_service`` and its ``parent_span_id`` must
    resolve, in the same trace, to a Span owned by ``parent_service``.  Keep
    both normal and ERROR child Spans; callers decide which status to count.

    The helper deliberately receives only public Trace rows and service names.
    It does not read files, execute SQL, or know any question ID/GT value.
    """
    raise TodoNotImplemented("TODO-05")
