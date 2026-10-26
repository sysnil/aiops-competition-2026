"""Pure DRAFT model for immutable first/+60/+120 and +240 trace audit."""

import hashlib
import json


def _freeze(rows):
    unique = {}
    duplicates = 0
    for identity, content_sha in rows:
        if identity in unique:
            if unique[identity] != content_sha:
                raise ValueError("same trace/span ID has conflicting content")
            duplicates += 1
        unique[identity] = content_sha
    return unique, duplicates


def _decision(status, reason, final=None, duplicates=0, late=None):
    final = final or {}
    value = {"status": status, "reason": reason,
             "deduplicated_identical_source_rows": duplicates,
             "late_ids": sorted(late or []),
             "scoring_archive_identities": len(final),
             "scoring_archive_sha256": hashlib.sha256(json.dumps(final,
                 sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
    return value


def assess(scenario):
    if not scenario.get("same_exact_utc_microsecond_window", False) or \
            scenario.get("cross_phase_overlap", False):
        return _decision("CAPTURE_INCOMPLETE_UTC_BOUNDARY", "window_or_phase_overlap")
    if not scenario.get("same_index_uuid_and_mapping", False) or any(
            scenario.get(stage + "_source_complete") is not True for stage in
            ("first", "plus60", "plus120", "audit240")):
        return _decision("CAPTURE_INCOMPLETE_SOURCE", "source_query_or_index_unreliable")
    first = scenario.get("first", {})
    plus60 = scenario.get("plus60", {})
    plus120 = scenario.get("plus120", {})
    audit = scenario.get("audit240", {})
    if (first.get("start") is None or first.get("finish") is None or
            first["start"] < 5 or first["finish"] > 90 or
            first["finish"] < first["start"] or
            plus60.get("start") is None or plus60.get("finish") is None or
            plus60["start"] < max(60, first["finish"]) or
            plus60["finish"] > 120 or plus60["finish"] < plus60["start"] or
            plus120.get("start") is None or plus120.get("finish") is None or
            plus120["start"] < max(120, plus60["finish"]) or
            plus120["finish"] > 180 or plus120["finish"] < plus120["start"] or
            audit.get("start") is None or audit.get("finish") is None or
            audit["start"] < 240 or audit["start"] > 270 or
            audit["finish"] > 300 or audit["finish"] < audit["start"]):
        return _decision("CAPTURE_INCOMPLETE_TIMEOUT", "fixed_schedule_or_deadline_failed")
    try:
        original, d1 = _freeze(first["rows"])
        middle, d2 = _freeze(plus60["rows"])
        final, d3 = _freeze(plus120["rows"])
        later, d4 = _freeze(audit["rows"])
    except (KeyError, ValueError) as exc:
        return _decision("CAPTURE_INCOMPLETE_CONFLICT", str(exc))
    duplicates = d1 + d2 + d3 + d4
    for earlier, newer in ((original, middle), (middle, final), (final, later)):
        if not earlier.keys() <= newer.keys():
            return _decision("CAPTURE_INCOMPLETE_IDENTITY_LOSS",
                             "earlier trace/span ID missing from later query",
                             final, duplicates)
        if any(earlier[key] != newer[key] for key in earlier):
            return _decision("CAPTURE_INCOMPLETE_CONFLICT",
                             "same ID changed content between immutable versions",
                             final, duplicates)
    late = later.keys() - final.keys()
    classes = scenario.get("late_classes", {})
    critical = {key for key in late if classes.get(key) in
                ("ORDINARY_BUSINESS", "FIXED_CONTROL", "ROOT_DIRECT_EVIDENCE")}
    if critical:
        return _decision("CAPTURE_INCOMPLETE_CRITICAL_LATE",
                         "late critical public evidence after frozen scoring snapshot",
                         final, duplicates, critical)
    uncertain = {key for key in late if classes.get(key) !=
                 "VERIFIED_NONCRITICAL_CONTROL_PLANE"}
    if uncertain:
        return _decision("CAPTURE_PARTIAL_UNVERIFIED",
                         "late evidence has no independently verified noncritical class",
                         final, duplicates, uncertain)
    if late:
        return _decision("BOUNDED_COMPLETION_WITH_NONCRITICAL_LATE_AUDIT",
                         "late control-plane IDs recorded without changing scoring snapshot",
                         final, duplicates, late)
    if original == final:
        return _decision("LIVE_COMPLETE", "first capture stable through audit",
                         final, duplicates)
    return _decision("BOUNDED_DELAY_COMPLETE",
                     "immutable supplements completed before deadline",
                     final, duplicates)
