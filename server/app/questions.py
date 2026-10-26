from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .storage import StorageError


QUESTION_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]{1,31}$")
VISIBLE_STATUS = {
    "practice": "PRACTICE_PUBLISHED",
    "semifinal": "SEMIFINAL_PUBLISHED",
}


class QuestionCatalog:
    """Reloadable public question definitions.

    The catalog never contains standard answers or scoring tolerances.  Formal
    templates may remain unassigned and are invisible until an organizer
    validates their data and explicitly changes the publication status.
    """

    def __init__(self, path: Path):
        self.path = path
        self._mtime_ns = -1
        self._items: dict[str, dict[str, Any]] = {}

    def _reload(self) -> None:
        stat = self.path.stat()
        if stat.st_mtime_ns == self._mtime_ns:
            return
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        items = payload.get("questions", [])
        if not isinstance(items, list):
            raise ValueError("question catalog must contain a questions array")
        validated: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("question entry must be an object")
            question_id = str(item.get("question_id", ""))
            if not QUESTION_ID_RE.fullmatch(question_id) or question_id in validated:
                raise ValueError(f"invalid or duplicate question_id: {question_id}")
            status = str(item.get("publication_status", ""))
            case_id = item.get("case_id")
            if status in VISIBLE_STATUS.values() and not isinstance(case_id, str):
                raise ValueError(f"published question {question_id} has no case_id")
            # The legacy catalog names the prompts ``three_questions`` while
            # the frozen semifinal delivery uses ``questions``.  Accept both
            # public formats, but still require exactly three prompts.
            questions = item.get("three_questions", item.get("questions"))
            if not isinstance(questions, list) or len(questions) != 3:
                raise ValueError(f"question {question_id} must define exactly three questions")
            validated[question_id] = item
        self._items = validated
        self._mtime_ns = stat.st_mtime_ns

    def templates(self) -> list[dict[str, Any]]:
        self._reload()
        return [dict(item) for item in self._items.values()]

    def list_visible(self, mode: str, published_cases: set[str], *, include_preview: bool = False) -> list[dict[str, Any]]:
        self._reload()
        expected = VISIBLE_STATUS.get(mode)
        if expected is None:
            raise StorageError("MODE_NOT_FOUND", "competition mode does not exist", 404)
        statuses = {expected}
        if mode == "semifinal" and include_preview:
            statuses.add("SEMIFINAL_PREVIEW")
        return [
            self._public(item)
            for item in sorted(self._items.values(), key=lambda value: str(value["question_id"]))
            if item.get("mode") == mode
            and item.get("publication_status") in statuses
            and item.get("case_id") in published_cases
        ]

    def get_visible(self, mode: str, question_id: str, published_cases: set[str], *, include_preview: bool = False) -> dict[str, Any]:
        if not QUESTION_ID_RE.fullmatch(question_id):
            raise StorageError("INVALID_QUESTION_ID", "question_id format is invalid", 400)
        self._reload()
        item = self._items.get(question_id)
        expected = VISIBLE_STATUS.get(mode)
        statuses = {expected}
        if mode == "semifinal" and include_preview:
            statuses.add("SEMIFINAL_PREVIEW")
        if (
            not item
            or expected is None
            or item.get("mode") != mode
            or item.get("publication_status") not in statuses
            or item.get("case_id") not in published_cases
        ):
            raise StorageError("QUESTION_NOT_FOUND", "question is not published or not authorized", 404)
        return self._public(item)

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        # Explicit projection prevents accidental publication if organizers add
        # private notes to the catalog in the future.
        fields = (
            "question_id", "case_id", "title", "description", "target_entity_type",
            "target_entity_id", "data_modality", "metric_or_operation",
            "start_time", "end_time", "chart_requirement", "three_questions",
            "data_processing_rule", "question_version", "publication_status",
            # Frozen semifinal-query-visualization v1 fields.  These are
            # deliberately enumerated so future private fields cannot leak.
            "modality", "time_range", "query_spec", "questions", "difficulty",
        )
        return {key: item[key] for key in fields if key in item}
