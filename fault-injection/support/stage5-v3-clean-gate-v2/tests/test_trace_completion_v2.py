import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from trace_completion_simulator import assess


def fixture():
    return {"same_exact_utc_microsecond_window": True,
        "same_index_uuid_and_mapping": True, "cross_phase_overlap": False,
        "first_source_complete": True, "plus60_source_complete": True,
        "plus120_source_complete": True, "audit240_source_complete": True,
        "first": {"start": 5, "finish": 35, "rows": [("t1/s1", "h1")]},
        "plus60": {"start": 60, "finish": 85, "rows": [("t1/s1", "h1")]},
        "plus120": {"start": 120, "finish": 150, "rows": [("t1/s1", "h1")]},
        "audit240": {"start": 240, "finish": 260, "rows": [("t1/s1", "h1")]},
        "late_classes": {}}


class TraceCompletionV2Tests(unittest.TestCase):
    def test_stable_first_capture(self):
        self.assertEqual(assess(fixture())["status"], "LIVE_COMPLETE")

    def test_plus60_late_and_plus120_late_are_immutably_counted(self):
        case = fixture()
        case["plus60"]["rows"].append(("t2/s2", "h2"))
        case["plus120"]["rows"].extend([("t2/s2", "h2"), ("t3/s3", "h3")])
        case["audit240"]["rows"].extend([("t2/s2", "h2"), ("t3/s3", "h3")])
        result = assess(case)
        self.assertEqual(result["status"], "BOUNDED_DELAY_COMPLETE")
        self.assertEqual(result["scoring_archive_identities"], 3)

    def test_late_after_120_verified_internal_is_not_silent(self):
        case = fixture()
        case["audit240"]["rows"].append(("flag/s2", "h2"))
        case["late_classes"]["flag/s2"] = "VERIFIED_NONCRITICAL_CONTROL_PLANE"
        self.assertEqual(assess(case)["status"],
                         "BOUNDED_COMPLETION_WITH_NONCRITICAL_LATE_AUDIT")

    def test_late_root_or_control_evidence_fails(self):
        for kind in ("ORDINARY_BUSINESS", "FIXED_CONTROL", "ROOT_DIRECT_EVIDENCE"):
            case = fixture()
            case["audit240"]["rows"].append(("t2/s2", "h2"))
            case["late_classes"]["t2/s2"] = kind
            self.assertEqual(assess(case)["status"],
                             "CAPTURE_INCOMPLETE_CRITICAL_LATE")

    def test_unclassified_late_evidence_fails_closed(self):
        case = fixture()
        case["audit240"]["rows"].append(("unknown/s2", "h2"))
        self.assertEqual(assess(case)["status"], "CAPTURE_PARTIAL_UNVERIFIED")

    def test_identical_duplicate_is_recorded_but_conflict_fails(self):
        case = fixture()
        case["plus60"]["rows"].append(("t1/s1", "h1"))
        self.assertEqual(assess(case)["deduplicated_identical_source_rows"], 1)
        case["plus60"]["rows"].append(("t1/s1", "different"))
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_CONFLICT")

    def test_content_conflict_between_versions_fails(self):
        case = fixture()
        case["plus120"]["rows"] = [("t1/s1", "different")]
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_CONFLICT")

    def test_missing_id_source_failure_and_utc_error(self):
        case = fixture()
        case["plus120"]["rows"] = []
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_IDENTITY_LOSS")
        case = fixture()
        case["audit240_source_complete"] = False
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_SOURCE")
        case = fixture()
        case["same_exact_utc_microsecond_window"] = False
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_UTC_BOUNDARY")

    def test_index_lag_and_cross_phase_duplicate_fail(self):
        case = fixture()
        case["same_index_uuid_and_mapping"] = False
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_SOURCE")
        case = fixture()
        case["cross_phase_overlap"] = True
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_UTC_BOUNDARY")

    def test_slow_query_and_supplement_timeout_fail(self):
        case = fixture()
        case["first"]["finish"] = 95
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_TIMEOUT")
        case = fixture()
        case["plus120"]["finish"] = 181
        self.assertEqual(assess(case)["status"], "CAPTURE_INCOMPLETE_TIMEOUT")


if __name__ == "__main__":
    unittest.main()
