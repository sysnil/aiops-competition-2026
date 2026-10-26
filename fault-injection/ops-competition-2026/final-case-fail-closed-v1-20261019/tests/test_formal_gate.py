import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[2]
SHARED = BASE / 'final-case-fail-closed-v1-20261019/scripts'
FORMAL = BASE / 'sf013-adapter-v4-20261019/scripts'
sys.path.insert(0, str(FORMAL))
sys.path.insert(0, str(SHARED))

import worker_auth
import fault_worker


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class FormalGateTests(unittest.TestCase):
    def test_direct_adapter_run_is_disabled(self):
        adapter = module(FORMAL / 'qualified_adapter.py', 'v4_qualified_adapter')
        with self.assertRaisesRegex(RuntimeError, 'LIVE_AUTH_ENTRY_CALLBACK_REQUIRED'):
            adapter.run(None, None, None)

    def test_worker_network_timeout_never_reaches_action(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            binding = {'run_id': 'SF013-AUTH-UNIT',
                       'action_id': 'SF013-AUTH-UNIT-POD',
                       'manifest_sha256': 'a' * 64,
                       'capability': 'MARKER_ONLY',
                       'worker_host': 'k8s-worker2',
                       'worker_boot_id': 'b' * 36,
                       'master_boot_id': 'c' * 36}
            called = []
            with patch.object(worker_auth.socket, 'gethostname',
                              return_value='k8s-worker2'), \
                 patch.object(worker_auth, 'boot_id', return_value='b' * 36), \
                 patch.object(worker_auth, 'ssh_command', return_value=['ssh']), \
                 patch.object(worker_auth.subprocess, 'run',
                              side_effect=subprocess.TimeoutExpired(['ssh'], 1.8)):
                with self.assertRaises(subprocess.TimeoutExpired):
                    worker_auth.execute_once(binding, runtime,
                                             lambda grant: called.append(grant))
            self.assertEqual(called, [])
            self.assertFalse((runtime / 'grant-consumed.json').exists())

    def test_worker_local_revocation_prevents_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            (runtime / 'REVOKED.json').write_text('{}')
            binding = {'worker_boot_id': 'b' * 36,
                       'worker_host': 'k8s-worker2'}
            with patch.object(worker_auth.socket, 'gethostname',
                              return_value='k8s-worker2'), \
                 patch.object(worker_auth.subprocess, 'run') as transport:
                with self.assertRaisesRegex(RuntimeError, 'LOCAL_REVOKED'):
                    worker_auth.execute_once(binding, runtime, lambda grant: None)
                transport.assert_not_called()

    def test_formal_fire_rejects_before_adapter_when_live_auth_denies(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder)
            manifest = {'run_id': 'SF013-AUTH-UNIT'}
            plan = {'source_sha256': {}}
            action = {'action_id': 'SF013-AUTH-UNIT-POD'}
            args = type('Args', (), {'manifest_sha': 'a' * 64})()
            binding = {'run_id': manifest['run_id'],
                       'action_id': action['action_id']}
            with patch.object(fault_worker, 'bind',
                              return_value=(manifest, plan, action, runtime)), \
                 patch.object(fault_worker, 'binding_for',
                              return_value=binding), \
                 patch.object(fault_worker, 'execute_once',
                              side_effect=RuntimeError('LIVE_AUTH_DENIED')):
                with self.assertRaises(SystemExit):
                    fault_worker.fire(args)
            terminal = json.loads((runtime / 'terminal.json').read_text())
            self.assertEqual(terminal['status'], 'FORMAL_FAULT_FAILED_CLOSED')
            self.assertIn('LIVE_AUTH_DENIED', terminal['error'])
            self.assertTrue((runtime / 'started.json').exists())
            self.assertFalse((runtime / 'actual-start.json').exists())


if __name__ == '__main__':
    unittest.main()
