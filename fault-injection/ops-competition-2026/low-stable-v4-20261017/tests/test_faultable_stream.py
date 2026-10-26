import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch


MODULE = Path(__file__).resolve().parents[1] / 'formal/fixed_business_streams_faultable.py'
spec = importlib.util.spec_from_file_location('fixed_business_streams_faultable', MODULE)
stream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stream)


class FaultableStreamTests(unittest.TestCase):
    def test_authorized_home_request_emits_header_and_live_identity(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'ok'

        with tempfile.TemporaryDirectory() as folder:
            at = datetime.now(timezone.utc)
            output = Path(folder)
            lease_path = output / 'lease.json'
            contract = {'operation': 'home', 'run_id': 'SF-004-ATTEMPT-02',
                        'plan_sha256': 'registered-plan',
                        'start_utc': (at - timedelta(seconds=1)).isoformat(),
                        'end_utc': (at + timedelta(seconds=60)).isoformat(),
                        'delay_ms': 250, 'lease_path': str(lease_path)}
            lease_path.write_text(json.dumps({
                'run_id': contract['run_id'], 'plan_sha256': contract['plan_sha256'],
                'permission': 'BOUND_HTTP_REQDELAY', 'safety_pass': True,
                'issued_utc': (at - timedelta(seconds=1)).isoformat(),
                'expires_utc': (at + timedelta(seconds=2)).isoformat()}))
            with patch.object(stream.urllib.request, 'urlopen', return_value=Response()) as send:
                result = stream.request('http://example', 'home', 0, 123, 8,
                                        contract, output, threading.Lock())
            sent = send.call_args.args[0]
            self.assertEqual(sent.get_header('X-envoy-fault-delay-request'), '250')
            self.assertTrue(result['success'])
            self.assertEqual(json.loads((output / 'control-events-live.jsonl').read_text())['trace_id'],
                             result['trace_id'])
            self.assertTrue(json.loads((output / 'home-header-events.jsonl').read_text())[
                'delay_header_applied'])

    def test_only_home_inside_window_with_fresh_bound_lease_gets_delay(self):
        with tempfile.TemporaryDirectory() as folder:
            at = datetime.now(timezone.utc)
            lease_path = Path(folder) / 'lease.json'
            contract = {'operation': 'home', 'run_id': 'SF-004-ATTEMPT-02',
                        'plan_sha256': 'registered-plan',
                        'start_utc': (at - timedelta(seconds=1)).isoformat(),
                        'end_utc': (at + timedelta(seconds=60)).isoformat(),
                        'delay_ms': 250, 'lease_path': str(lease_path)}
            lease = {'run_id': contract['run_id'], 'plan_sha256': contract['plan_sha256'],
                     'permission': 'BOUND_HTTP_REQDELAY', 'safety_pass': True,
                     'issued_utc': (at - timedelta(seconds=1)).isoformat(),
                     'expires_utc': (at + timedelta(seconds=2)).isoformat()}
            lease_path.write_text(json.dumps(lease))
            self.assertEqual(stream.fault_delay(contract, 'home', at.timestamp()), 250)
            self.assertIsNone(stream.fault_delay(contract, 'cart_view', at.timestamp()))
            self.assertIsNone(stream.fault_delay(contract, 'home', at.timestamp() - 5))
            lease['run_id'] = 'OTHER-RUN'
            lease_path.write_text(json.dumps(lease))
            with self.assertRaisesRegex(RuntimeError, 'invalid or expired'):
                stream.fault_delay(contract, 'home', at.timestamp())

    def test_missing_lease_fails_closed(self):
        at = datetime.now(timezone.utc)
        contract = {'start_utc': (at - timedelta(seconds=1)).isoformat(),
                    'end_utc': (at + timedelta(seconds=1)).isoformat(),
                    'lease_path': '/nonexistent/formal-http-lease'}
        with self.assertRaises(OSError):
            stream.fault_delay(contract, 'home', at.timestamp())


if __name__ == '__main__':
    unittest.main()
