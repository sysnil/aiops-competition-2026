"""Controller-loss watcher shared by Marker and future formal guard."""

import json
import subprocess
import time
from pathlib import Path


def active(unit):
    call = subprocess.run(['systemctl', 'is-active', unit + '.service'],
                          capture_output=True, text=True, timeout=4)
    return call.returncode == 0 and call.stdout.strip() == 'active'


def watch(controller_unit, started, terminal, deadline, cancel,
          poll_seconds=.2, startup_grace=5):
    began = time.monotonic()
    seen = Path(started).exists()
    while time.time() < deadline:
        running = active(controller_unit)
        seen |= running or Path(started).exists()
        if Path(terminal).exists():
            controller = json.loads(Path(terminal).read_text())
            return {'status': 'GUARD_CONTROLLER_TERMINAL_REVOKED',
                    'controller_status': controller['status'],
                    'worker_cancel': cancel()}
        if not running and (seen or time.monotonic() - began >= startup_grace):
            return {'status': 'GUARD_ORPHAN_REVOKED',
                    'worker_cancel': cancel()}
        time.sleep(poll_seconds)
    return {'status': 'GUARD_DEADLINE_REVOKED', 'worker_cancel': cancel()}
