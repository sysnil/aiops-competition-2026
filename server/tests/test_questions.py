from __future__ import annotations

import json

from app.questions import QuestionCatalog
from app.storage import StorageError


def test_only_published_mode_and_case_are_visible(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [
        {"question_id": "P-Q01", "mode": "practice", "case_id": "SF-001", "publication_status": "PRACTICE_PUBLISHED", "three_questions": [{}, {}, {}]},
        {"question_id": "Q01", "mode": "semifinal", "case_id": None, "publication_status": "TEMPLATE_NOT_DATA_VALIDATED", "three_questions": [{}, {}, {}]},
    ]}), encoding="utf-8")
    catalog = QuestionCatalog(path)
    assert [item["question_id"] for item in catalog.list_visible("practice", {"SF-001"})] == ["P-Q01"]
    assert catalog.list_visible("semifinal", {"SF-003"}) == []


def test_unpublished_question_is_indistinguishable(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [
        {"question_id": "Q01", "mode": "semifinal", "case_id": None, "publication_status": "TEMPLATE_NOT_DATA_VALIDATED", "three_questions": [{}, {}, {}]},
    ]}), encoding="utf-8")
    catalog = QuestionCatalog(path)
    try:
        catalog.get_visible("semifinal", "Q01", {"SF-003"})
    except StorageError as exc:
        assert (exc.code, exc.status_code) == ("QUESTION_NOT_FOUND", 404)
    else:
        raise AssertionError("unpublished question leaked")


def test_frozen_public_question_is_visible_only_in_internal_preview(tmp_path):
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [{
        "question_id": "Q01", "mode": "semifinal", "case_id": "SF-003",
        "publication_status": "SEMIFINAL_PREVIEW", "questions": [{}, {}, {}],
        "query_spec": {"kind": "memory"}, "description": "public description",
    }]}), encoding="utf-8")
    catalog = QuestionCatalog(path)
    assert catalog.list_visible("semifinal", {"SF-003"}) == []
    visible = catalog.get_visible("semifinal", "Q01", {"SF-003"}, include_preview=True)
    assert visible["query_spec"] == {"kind": "memory"}
    assert visible["description"] == "public description"
