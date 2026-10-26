"""Pure fail-closed checks for Collector and NetworkPath rehearsal evidence."""

import math


def counter_change(before, after, same_instance=True):
    if not same_instance:
        return {"status": "MIXED_COLLECTOR_INSTANCE", "increase": None}
    if before is None or after is None or not all(math.isfinite(value)
            for value in (before, after)):
        return {"status": "METRIC_MISSING", "increase": None}
    if after < before:
        return {"status": "COUNTER_RESET", "increase": None}
    return {"status": "VALID", "increase": after - before}


def refused_span_outcome(refused, trace_proof):
    if not refused:
        return "NO_REFUSAL_OBSERVED"
    if trace_proof == "same_span_id_finally_persisted":
        return "REFUSAL_RETRIED_AND_PERSISTED_CONFIRMED"
    if trace_proof == "span_generated_and_export_failed_finally_absent":
        return "PERMANENT_LOSS_CONFIRMED"
    return "REFUSAL_OUTCOME_UNRESOLVED"


def trace_window_status(issued, proxy, frontend, critical_downstream,
                        direct_uid_verified, late_critical):
    if issued is None or proxy is None or frontend is None or critical_downstream is None:
        return "CAPTURE_INCOMPLETE"
    if proxy < issued or frontend < issued or critical_downstream < issued:
        return "TRACE_COVERAGE_FAIL"
    if not direct_uid_verified or late_critical:
        return "TRACE_COMPLETION_OR_UID_FAIL"
    return "OBSERVED_WINDOW_PASS"


def second_control_status(*, business_packets, client_server_spans,
                          path_excludes_filtered_interface, shared_target_resource,
                          stable_minute_counts, stable_latency):
    if not business_packets or not client_server_spans or not path_excludes_filtered_interface:
        return "NOT_QUALIFIED"
    if shared_target_resource or not stable_minute_counts or not stable_latency:
        return "CONDITIONAL"
    return "QUALIFIED"


def old_path_usable(*, pod_uids_unchanged, endpoints_unchanged,
                    fresh_business_packets, route_unchanged):
    return all((pod_uids_unchanged, endpoints_unchanged,
                fresh_business_packets, route_unchanged))


def real_experiment_allowed(*, safety_pass, protocol_approved, no_faults_active,
                            rollback_ready):
    return all((safety_pass, protocol_approved, no_faults_active, rollback_ready))


def source_release_allowed(*, worker2_verified, worker3_verified,
                           restored_read, source_unchanged):
    return all((worker2_verified, worker3_verified, restored_read,
                source_unchanged))
