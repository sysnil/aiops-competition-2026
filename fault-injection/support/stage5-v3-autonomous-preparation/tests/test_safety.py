"""Offline checks for bounded adapters and preregistration invariants."""

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import freeze_registration
import build_registration
import audit_hosts
import assess_attempt
import network_fault
import probe_network
import resource_fault


class SafetyTests(unittest.TestCase):
    def test_fault_registration_needs_both_clean_mechanism_preconditions(self):
        key = 'NODE_CPU_WORKER1'
        passed = {'mechanisms': {key: {'status': 'PRECONDITION_PASS'}}}
        blocked = {'mechanisms': {key: {'status': 'BLOCKED'}}}
        self.assertEqual(build_registration.repeated_precondition(
            passed, passed, key), 'PASS')
        self.assertEqual(build_registration.repeated_precondition(
            passed, blocked, key), 'BLOCKED')

    def test_pod_memory_stays_below_eighty_percent_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            limit = 500 * resource_fault.MIB
            current = 50 * resource_fault.MIB
            (root / 'memory.max').write_text(str(limit))
            (root / 'memory.current').write_text(str(current))
            amount = resource_fault.memory_bytes('POD', root)
            self.assertGreaterEqual(amount, 32 * resource_fault.MIB)
            self.assertLessEqual(current + amount, int(limit * 0.80))

    def test_pod_memory_rejects_insufficient_headroom(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'memory.max').write_text(str(500 * resource_fault.MIB))
            (root / 'memory.current').write_text(str(390 * resource_fault.MIB))
            with self.assertRaises(ValueError):
                resource_fault.memory_bytes('POD', root)

    def test_network_registration_rejects_wrong_strength_and_path(self):
        spec = {'id': 'S5V3AP-NETWORK-S1',
                'mechanism': 'egress_filtered_netem_loss',
                'path': 'NETWORK_PATH/k8s-worker3/k8s-worker2',
                'fault_seconds': 300, 'loss_percent': 10}
        self.assertFalse(network_fault.spec_not_registered(spec))
        for field, value in (('fault_seconds', 360), ('loss_percent', 20),
                             ('path', 'NETWORK_PATH/k8s-worker2/k8s-worker3')):
            changed = {**spec, field: value}
            self.assertTrue(network_fault.spec_not_registered(changed))

    def test_probe_timeout_is_missing_not_zero(self):
        row = probe_network.empty('TIMEOUT')
        self.assertIsNone(row['throughput_bps'])
        self.assertIsNone(row['loss_fraction'])
        self.assertIsNone(row['jitter_ms'])

    def test_resource_adapter_rejects_unregistered_mechanism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / 'manifest.json'
            manifest.write_text(json.dumps({
                'status': 'QUALIFICATION_REGISTRATION_FROZEN_PRE_FAULT'}))
            preflight = root / 'preflight.json'
            preflight.write_text(json.dumps({
                'status': 'QUALIFICATION_PREFLIGHT_FROZEN_PRE_FAULT',
                'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'design_freeze_sha256': resource_fault.sha256(manifest),
                'root_layer': 'POD', 'family': 'cpu_quota_throttling',
                'case_id': 'S5V3AP-POD-S1', 'gt_read': False,
                'faults_injected': 0}))
            with self.assertRaises(ValueError):
                resource_fault.precheck(manifest, preflight)

    def test_repeated_seed_cannot_change_fault_strength(self):
        families = {'SERVICE': 'process_stall', 'POD': 'memory_pressure',
                    'NODE': 'cpu_contention',
                    'NETWORK_PATH': 'egress_filtered_netem_loss'}
        rows = []
        for layer, family in families.items():
            for number in (1, 2):
                prefix = 'NETWORK' if layer == 'NETWORK_PATH' else layer
                rows.append({'id': f'S5V3AP-{prefix}-S{number}', 'layer': layer,
                             'family': family, 'seed': len(rows) + 1,
                             'fault_seconds': 300, 'formal_case': False,
                             'load_plan_sha256': 'unchanged',
                             'intensity': {'amount': 10},
                             'precondition_status': 'PASS'})
        plan = {'experiments': rows, 'stage5_v3_protocol_frozen': False,
                'formal_case_created': False,
                'faults_injected_before_registration': 0,
                'clean_evidence_paths': [str(freeze_registration.ROOT /
                    'results' / f'clean-{index}.json') for index in range(6)]}
        freeze_registration.verify_plan(plan)
        rows[1]['intensity'] = {'amount': 20}
        with self.assertRaises(ValueError):
            freeze_registration.verify_plan(plan)

    def test_only_current_receiver_unit_is_temporarily_exempt(self):
        expected = 's5v3ap-iperf-s5v3ap-service-s1.service'
        def remote_with(unit):
            def answer(_ip, _socket, *command):
                if command[:2] == ('ps', '-eo'):
                    return ''
                if command[:2] == ('tc', 'qdisc'):
                    return 'qdisc mq 0: root\n'
                if command[:2] == ('systemctl', 'list-units'):
                    return unit + ' loaded active running\n' if '--type=service' in command else ''
                raise AssertionError(command)
            return answer
        with patch.object(audit_hosts, 'remote', side_effect=remote_with(expected)):
            row = audit_hosts.audit('k8s-worker2', '192.0.2.2', '/tmp/synthetic',
                                    expected_probe_unit=expected)
            self.assertEqual(row['status'], 'PASS')
        with patch.object(audit_hosts, 'remote', side_effect=remote_with(
                's5v3ap-iperf-s5v3ap-network-s2.service')):
            row = audit_hosts.audit('k8s-worker2', '192.0.2.2', '/tmp/synthetic',
                                    expected_probe_unit=expected)
            self.assertEqual(row['status'], 'FAIL')

    def test_extended_proxy_watchdog_is_required_and_narrowly_exempt(self):
        def remote_with(timer):
            def answer(_ip, _socket, *command):
                if command[:2] == ('ps', '-eo'):
                    return ''
                if command[:2] == ('tc', 'qdisc'):
                    return 'qdisc mq 0: root\n'
                if command[:2] == ('systemctl', 'list-units'):
                    return (timer + ' loaded active waiting\n'
                            if '--type=timer' in command else '')
                raise AssertionError(command)
            return answer
        expected = 's5v3ap-proxy-rollback-extended.timer'
        with patch.object(audit_hosts, 'remote', side_effect=remote_with(expected)):
            self.assertEqual(audit_hosts.audit('k8s-master', '192.0.2.1',
                '/tmp/synthetic', expected_proxy_watchdog=True)['status'], 'PASS')
        with patch.object(audit_hosts, 'remote', side_effect=remote_with('')):
            self.assertEqual(audit_hosts.audit('k8s-master', '192.0.2.1',
                '/tmp/synthetic', expected_proxy_watchdog=True)['status'], 'FAIL')

    def test_business_impact_none_and_strong_are_separate_from_admission(self):
        spec = {'impact_envelope': {'operation': 'recommendations',
                'minimum_attempted_during_five_minutes': 150,
                'clean_max_minute_error_fraction': 0.0,
                'clean_max_minute_p95_ratio': 1.25}}
        rows = [{'attempted': 50, 'successful': 50,
                 'client_p95_ms': 100.0} for _ in range(25)]
        analysis = {'state_machine_client': {'state_machine/recommendations': rows}}
        self.assertEqual(assess_attempt.impact(analysis, spec)['label'], 'NONE')
        for row in rows[10:15]:
            row['successful'] = 40
            row['client_p95_ms'] = 200.0
        self.assertEqual(assess_attempt.impact(analysis, spec)['label'], 'STRONG')
        for row in rows[10:15]:
            row['attempted'] = 1
        self.assertEqual(assess_attempt.impact(analysis, spec)['label'],
                         'UNMEASURABLE')

    def test_network_control_requires_both_coverage_and_stable_latency(self):
        client = [{'successful': 60,
                   'client_p95_to_clean_median': 1.0} for _ in range(25)]
        analysis = {
            'fixed_control_client': {'image_asset': [dict(row) for row in client],
                                     'cart_view': [dict(row) for row in client]},
            'fixed_control_edge_trace_ids_per_minute': {
                'image_asset/frontend-proxy->image-provider': [60] * 25,
                'cart_view/frontend->cart': [60] * 25}}
        self.assertTrue(all(row['pass'] for row in
                            assess_attempt.controls(analysis).values()))
        analysis['fixed_control_client']['cart_view'][12][
            'client_p95_to_clean_median'] = 1.3
        self.assertFalse(assess_attempt.controls(analysis)['cart_view']['pass'])


if __name__ == '__main__':
    unittest.main()
