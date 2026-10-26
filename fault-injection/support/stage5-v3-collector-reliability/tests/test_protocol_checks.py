import unittest

from scripts.protocol_checks import (counter_change, old_path_usable,
    real_experiment_allowed, refused_span_outcome, second_control_status,
    source_release_allowed, trace_window_status)


class CollectorProtocolTests(unittest.TestCase):
    def test_refused_then_retried_successfully(self):
        self.assertEqual(refused_span_outcome(8, "same_span_id_finally_persisted"),
                         "REFUSAL_RETRIED_AND_PERSISTED_CONFIRMED")

    def test_refused_then_permanent_loss(self):
        self.assertEqual(refused_span_outcome(8,
            "span_generated_and_export_failed_finally_absent"), "PERMANENT_LOSS_CONFIRMED")

    def test_aggregate_refusal_alone_cannot_claim_loss(self):
        self.assertEqual(refused_span_outcome(1195, None), "REFUSAL_OUTCOME_UNRESOLVED")

    def test_receiver_metric_missing_is_not_zero(self):
        self.assertEqual(counter_change(None, 12)["status"], "METRIC_MISSING")

    def test_processor_metric_missing_is_not_zero(self):
        self.assertEqual(counter_change(12, None)["increase"], None)

    def test_exporter_metric_missing_is_not_zero(self):
        self.assertEqual(counter_change(float("nan"), 2)["status"], "METRIC_MISSING")

    def test_counter_reset_fails_closed(self):
        self.assertEqual(counter_change(40, 5)["status"], "COUNTER_RESET")

    def test_mixed_collector_instances_fail_closed(self):
        self.assertEqual(counter_change(3, 5, same_instance=False)["status"],
                         "MIXED_COLLECTOR_INSTANCE")

    def test_intermitten_consecutive_proxy_gap_is_failure(self):
        self.assertEqual(trace_window_status(100, 68, 100, 100, True, False),
                         "TRACE_COVERAGE_FAIL")

    def test_uid_missing_or_mismatch_fails(self):
        self.assertEqual(trace_window_status(100, 100, 100, 100, False, False),
                         "TRACE_COMPLETION_OR_UID_FAIL")

    def test_proxy_complete_but_downstream_missing_fails(self):
        self.assertEqual(trace_window_status(100, 100, 100, 99, True, False),
                         "TRACE_COVERAGE_FAIL")

    def test_critical_late_after_plus120_fails(self):
        self.assertEqual(trace_window_status(100, 100, 100, 100, True, True),
                         "TRACE_COMPLETION_OR_UID_FAIL")

    def test_shared_worker3_resource_is_not_qualified_control(self):
        self.assertEqual(second_control_status(business_packets=True,
            client_server_spans=True, path_excludes_filtered_interface=True,
            shared_target_resource=True, stable_minute_counts=True,
            stable_latency=True), "CONDITIONAL")

    def test_no_real_business_packets_rejects_second_control(self):
        self.assertEqual(second_control_status(business_packets=False,
            client_server_spans=True, path_excludes_filtered_interface=True,
            shared_target_resource=False, stable_minute_counts=True,
            stable_latency=True), "NOT_QUALIFIED")

    def test_old_route_invalid_after_pod_move(self):
        self.assertFalse(old_path_usable(pod_uids_unchanged=False,
            endpoints_unchanged=True, fresh_business_packets=True,
            route_unchanged=True))

    def test_unapproved_clean_gate_blocks_real_fault(self):
        self.assertFalse(real_experiment_allowed(safety_pass=True,
            protocol_approved=False, no_faults_active=True, rollback_ready=True))

    def test_global_safety_fail_blocks_real_experiment(self):
        self.assertFalse(real_experiment_allowed(safety_pass=False,
            protocol_approved=True, no_faults_active=True, rollback_ready=True))

    def test_dual_archive_failure_blocks_release(self):
        self.assertFalse(source_release_allowed(worker2_verified=True,
            worker3_verified=False, restored_read=True, source_unchanged=True))


if __name__ == "__main__":
    unittest.main()
