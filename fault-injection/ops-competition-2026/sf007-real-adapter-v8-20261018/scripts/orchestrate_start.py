"""One bounded, fail-closed launch path for SF-007 ATTEMPT-08."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time


OPS = Path(__file__).resolve().parents[2]
WORKSPACE = OPS.parent
ADAPTER = OPS / 'sf007-real-adapter-v8-20261018'
FORMAL = OPS / 'sf007-formal-v8-20261018'
RESULTS = OPS / 'low-stable-v4-20261017/results'
STORAGE = OPS / 'low-stable-v4-20261017/storage-v2'
RUN_ID = 'SF-007-ATTEMPT-08'
MASTER = '192.168.53.52'
WORKER3 = '192.168.53.55'
MASTER_SOCKET = '/tmp/sfgen-master.sock'
WORKER3_SOCKET = '/tmp/sfgen-w3.sock'
MANIFEST = Path('/root/ops-competition-2026/sf007-real-adapter-v8-20261018/plans') / RUN_ID / 'run-manifest.json'
PLAN = MANIFEST.with_name('run-plan.json')
RUN = Path('/data/stage5-v3/ops-competition-2026/runs') / RUN_ID
IDENTITY_REMOTE = Path('/root/ops-competition-2026/sf007-formal-v8-20261018/plans/current-identity.json')
STORAGE_REMOTE = Path('/root/ops-competition-2026/low-stable-v4-20261017/results/sf007-att08-storage-preflight.json')
RESIDUE_REMOTE = Path('/root/ops-competition-2026/low-stable-v4-20261017/results/sf007-att08-residue-preflight.json')
WATCHDOG_REMOTE = Path('/root/ops-competition-2026/qualification/SF007-WATCHDOG-SELFTEST-W3-ATTEMPT08.json')
IDENTITY_LOCAL = RESULTS / 'sf007-attempt08-identity.json'
PLAN_LOCAL = RESULTS / 'sf007-att08-run-plan.json'
HEARTBEAT_LOCAL = RESULTS / 'sf007-att08-five-host-live.jsonl'
CONTROLLER_UNIT = 'ops-sf007-formal-controller08-20261017.service'
CAPTURE_UNIT = 'ops-sf007-formal-capture08-20261017.service'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def local(*args):
    return subprocess.run(list(map(str, args)), cwd=WORKSPACE, capture_output=True,
                          text=True, check=True, timeout=60).stdout


def remote(*args):
    return local('ssh', '-S', MASTER_SOCKET, '-o', 'BatchMode=yes',
                 'root@' + MASTER, shlex.join(list(map(str, args))))


def scp(source, target, socket=MASTER_SOCKET):
    local('scp', '-q', '-o', 'BatchMode=yes', '-o', 'ControlPath=' + socket,
          str(source), str(target))


def start_age(identity, now):
    age = now - datetime.fromisoformat(identity['at_utc']).timestamp()
    if identity['status'] != 'CANDIDATE_CGROUPS_INSPECTED_NOT_QUALIFIED' or not 0 <= age <= 100:
        raise RuntimeError('Identity age exceeds the 100-second orchestration margin')
    return age


def launch_time(t0, now):
    target = t0 - 230
    if target < now - 5 or t0 - now < 200:
        raise RuntimeError('Registered T0 cannot support a fresh capture startup')
    return target


def main():
    monitor = None
    stdout = stderr = None
    started = completed = False
    try:
        if any(path.exists() for path in (IDENTITY_LOCAL, PLAN_LOCAL, HEARTBEAT_LOCAL)):
            raise RuntimeError('Orchestrator outputs already exist')
        local('python3', '-B', STORAGE / 'dynamic_storage_case_v13.py',
              '--contract', STORAGE / 'case-budgets/sf007-att08.json',
              '--output', RESULTS / 'sf007-att08-storage-preflight.json')
        local('python3', '-B', WORKSPACE / 'stage5-v2/scripts/host_residue_audit.py',
              '--host', 'k8s-master,192.168.53.52,/tmp/sfgen-master.sock',
              '--host', 'k8s-worker1,192.168.53.53,/tmp/sfgen-w1.sock',
              '--host', 'k8s-worker2,192.168.53.54,/tmp/sfgen-w2.sock',
              '--host', 'k8s-worker3,192.168.53.55,/tmp/sfgen-w3.sock',
              '--host', 'router,192.168.53.50,/tmp/sfgen-router.sock',
              '--output', RESULTS / 'sf007-att08-residue-preflight.json')
        remote('install', '-d', '-m', '700', IDENTITY_REMOTE.parent)
        scp(RESULTS / 'sf007-att08-storage-preflight.json',
            'root@' + MASTER + ':' + str(STORAGE_REMOTE))
        scp(RESULTS / 'sf007-att08-residue-preflight.json',
            'root@' + MASTER + ':' + str(RESIDUE_REMOTE))
        local('ssh', '-S', WORKER3_SOCKET, '-o', 'BatchMode=yes',
              'root@' + WORKER3, 'python3', '-B',
              '/root/ops-competition-2026/sf007-real-adapter-v8-20261018/scripts/watchdog_reachability.py')
        scp('root@' + WORKER3 + ':' + str(WATCHDOG_REMOTE),
            RESULTS / 'SF007-WATCHDOG-SELFTEST-W3-ATTEMPT08.json', WORKER3_SOCKET)
        scp(RESULTS / 'SF007-WATCHDOG-SELFTEST-W3-ATTEMPT08.json',
            'root@' + MASTER + ':' + str(WATCHDOG_REMOTE))

        # Identity is deliberately the final prerequisite before registration.
        local('python3', '-B', OPS / 'low-stable-v4-20261017/scripts/refresh_sf007_v2_identity.py',
              '--output', IDENTITY_LOCAL)
        scp(IDENTITY_LOCAL, 'root@' + MASTER + ':' + str(IDENTITY_REMOTE))
        prepare = '/root/ops-competition-2026/sf007-real-adapter-v8-20261018/scripts/prepare_sf005_formal.py'
        shared = ['python3', '-B', prepare, '--five-host-snapshot', STORAGE_REMOTE,
                  '--five-host-residue', RESIDUE_REMOTE, '--seed', '7310']
        remote(*shared, '--validate-only')
        registration = json.loads(remote(*shared))
        if registration['status'] != 'SF007_FORMAL_ATTEMPT_PREREGISTERED_NO_FAULT_YET':
            raise RuntimeError('Formal registration was not accepted')
        manifest_sha = registration['manifest_sha256']
        t0 = datetime.fromisoformat(registration['start_utc']).timestamp()
        scp('root@' + MASTER + ':' + str(PLAN), PLAN_LOCAL)
        plan_sha = sha(PLAN_LOCAL)
        monitor_script = ADAPTER / 'scripts/five_host_heartbeat.py'
        stdout = (RESULTS / 'sf007-att08-monitor.stdout').open('x')
        stderr = (RESULTS / 'sf007-att08-monitor.stderr').open('x')
        monitor = subprocess.Popen([sys.executable, '-B', str(monitor_script),
            '--plan', str(PLAN_LOCAL), '--plan-sha256', plan_sha,
            '--output', str(HEARTBEAT_LOCAL)], cwd=WORKSPACE,
            stdout=stdout, stderr=stderr)
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if monitor.poll() is not None:
                raise RuntimeError('Five-host storage monitor exited before first heartbeat')
            result = json.loads(remote('python3', '-c',
                'import json,pathlib; p=pathlib.Path(' + repr(str(RUN / 'control/five-host-storage.json')) +
                '); print(json.dumps(json.loads(p.read_text()).get("status") if p.is_file() else None))'))
            if result == 'DYNAMIC_STORAGE_PASS':
                break
            time.sleep(.5)
        else:
            raise RuntimeError('First five-host storage heartbeat timed out')
        time.sleep(max(0, launch_time(t0, time.time()) - time.time()))
        identity_age = start_age(json.loads(IDENTITY_LOCAL.read_text()), time.time())
        start = '/root/ops-competition-2026/sf007-real-adapter-v8-20261018/scripts/start_formal.py'
        args = ['python3', '-B', start, '--manifest', MANIFEST,
                '--manifest-sha', manifest_sha]
        validation = json.loads(remote(*args, '--validate-only'))
        if validation['status'] != 'STARTUP_COMMANDS_AND_CAPTURE_PREREQ_VALIDATED_NO_UNITS_CREATED':
            raise RuntimeError('Capture preflight did not pass before unit creation')
        started_result = json.loads(remote(*args))
        started = True
        if (started_result['status'] != 'FORMAL_UNITS_STARTED_PENDING_READY' or
                not started_result.get('capture_initial_health_sha256')):
            raise RuntimeError('Capture startup handshake did not pass')
        print(json.dumps({'status': 'SF007_ATT08_CAPTURE_READY_AND_CONTROLLER_STARTED',
                          'at_utc': utc(), 'identity_age_before_validation_seconds': identity_age,
                          'capture_initial_health_sha256': started_result['capture_initial_health_sha256'],
                          'manifest_sha256': manifest_sha}), flush=True)
        monitor_exit = monitor.wait(timeout=2700)
        scp(HEARTBEAT_LOCAL, 'root@' + MASTER + ':' + str(RUN / 'control/five-host-live.jsonl'))
        if monitor_exit:
            raise RuntimeError('Five-host storage monitor rejected the formal attempt')
        completed = True
        print(json.dumps({'status': 'SF007_ATT08_FORMAL_CAPTURE_MONITOR_COMPLETE',
                          'at_utc': utc(), 'run_id': RUN_ID}), flush=True)
    except BaseException as error:
        failure = {'status': 'SF007_ATT08_ORCHESTRATION_FAILED',
                   'at_utc': utc(), 'error': type(error).__name__ + ': ' + str(error),
                   'controller_started': started}
        path = RESULTS / 'sf007-att08-orchestration-failure.json'
        if not path.exists():
            with path.open('x') as stream:
                json.dump(failure, stream, indent=2)
                stream.write('\n')
        print(json.dumps(failure), flush=True)
        raise
    finally:
        if started and not completed and monitor is not None and monitor.poll() is None:
            subprocess.run(['ssh', '-S', MASTER_SOCKET, '-o', 'BatchMode=yes',
                            'root@' + MASTER, 'systemctl stop ' + CONTROLLER_UNIT],
                           capture_output=True, timeout=20)
            subprocess.run(['ssh', '-S', MASTER_SOCKET, '-o', 'BatchMode=yes',
                            'root@' + MASTER, 'systemctl stop ' + CAPTURE_UNIT],
                           capture_output=True, timeout=20)
        if monitor is not None and monitor.poll() is None:
            monitor.terminate()
            monitor.wait(timeout=20)
        if stdout is not None:
            stdout.close()
        if stderr is not None:
            stderr.close()


if __name__ == '__main__':
    main()
