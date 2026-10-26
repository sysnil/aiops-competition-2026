from __future__ import annotations

import copy
import unittest

from grading.private_grader.qa_scoring import score_qa_answer


class QAScoringTests(unittest.TestCase):
    def setUp(self):
        self.chart = {
            "chart_type": "line", "title": "chart",
            "x_axis": {"label": "time", "timezone": "UTC"},
            "y_axis": {"label": "memory", "unit": "MiB"},
            "series": [{"name": "pod-a", "unit": "MiB", "points": [
                {"timestamp": "2026-10-14T09:12:00Z", "value": 10.0},
                {"timestamp": "2026-10-14T09:13:00Z", "value": 12.0},
            ]}],
        }
        self.answers = [
            {"question_index": 1, "value": 12.0, "unit": "MiB", "timestamp": "2026-10-14T09:13:00Z"},
            {"question_index": 2, "value": 2.0, "unit": "MiB", "start_time": "2026-10-14T09:12:00Z", "end_time": "2026-10-14T09:13:00Z"},
            {"question_index": 3, "value": 1.0, "unit": "MiB"},
        ]
        self.gt = {
            "chart": self.chart, "answers": self.answers,
            "chart_value_tolerance": 0.001, "chart_time_tolerance_seconds": 0,
            "rubric_version": "test", "gt_version": "test",
        }
        self.answer = {
            "chart": copy.deepcopy(self.chart),
            "answers": [{**item, "answer_type": "test", "explanation": "calculated"} for item in self.answers],
        }

    def test_exact_answer_reaches_ten_points(self):
        result = score_qa_answer(self.answer, self.gt)
        self.assertEqual(result["score"], 10.0)
        self.assertEqual(result["breakdown"], {"chart": 4.0, "question_1": 2.0, "question_2": 2.0, "question_3": 2.0})

    def test_wrong_chart_data_does_not_receive_chart_data_points(self):
        self.answer["chart"]["series"][0]["points"][0]["value"] = 999.0
        result = score_qa_answer(self.answer, self.gt)
        self.assertEqual(result["breakdown"]["chart"], 1.5)
        self.assertEqual(result["score"], 7.5)

    def test_wrong_question_is_not_soft_scored(self):
        self.answer["answers"][1]["value"] = 999.0
        result = score_qa_answer(self.answer, self.gt)
        self.assertEqual(result["breakdown"]["question_2"], 0.0)
        self.assertEqual(result["score"], 8.0)

    def test_ten_independent_questions_can_reach_one_hundred(self):
        scores = [score_qa_answer(copy.deepcopy(self.answer), copy.deepcopy(self.gt))["score"] for _ in range(10)]
        self.assertEqual(scores, [10.0] * 10)
        self.assertEqual(sum(scores), 100.0)

    def test_matching_null_bucket_is_valid_but_cannot_hide_a_value(self):
        self.gt["chart"]["series"][0]["points"][0]["value"] = None
        self.answer["chart"]["series"][0]["points"][0]["value"] = None
        self.assertEqual(score_qa_answer(self.answer, self.gt)["breakdown"]["chart"], 4.0)
        self.answer["chart"]["series"][0]["points"][0]["value"] = 0.0
        self.assertEqual(score_qa_answer(self.answer, self.gt)["breakdown"]["chart"], 1.5)


if __name__ == "__main__":
    unittest.main()
