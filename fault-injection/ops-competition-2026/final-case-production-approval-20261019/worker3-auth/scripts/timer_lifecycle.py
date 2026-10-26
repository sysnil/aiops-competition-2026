"""Attempt-bound systemd timer registration and fail-closed revocation."""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from handshake import atomic


ACTIVE = {"active", "activating", "reloading"}


def now():
    return datetime.now(timezone.utc).isoformat()


def identity(run_id, action_id, manifest_sha256, unit):
    if (not run_id or not action_id.startswith(run_id + "-") or
            not unit.startswith("ops-sf014-") or
            not unit.endswith(action_id.lower()) or
            len(manifest_sha256) != 64):
        raise RuntimeError("ATTEMPT_TIMER_IDENTITY")
    return {"run_id": run_id, "action_id": action_id,
            "manifest_sha256": manifest_sha256, "unit": unit}


def state(unit):
    result = subprocess.run(
        ["systemctl", "show", unit, "--property=ActiveState",
         "--property=LoadState", "--no-pager"],
        capture_output=True, text=True, timeout=10)
    if result.returncode:
        raise RuntimeError("TIMER_STATE_QUERY_FAILED: " + result.stderr[-300:])
    properties = dict(line.split("=", 1) for line in result.stdout.splitlines()
                      if "=" in line)
    if not {"ActiveState", "LoadState"} <= properties.keys():
        raise RuntimeError("TIMER_STATE_INCOMPLETE")
    return properties


def inspect(unit):
    return {suffix: state(unit + suffix) for suffix in (".timer", ".service")}


def is_active(properties):
    return properties["ActiveState"] in ACTIVE


def _bound_record(path, expected):
    value = json.loads(path.read_text())
    if any(value.get(key) != item for key, item in expected.items()):
        raise RuntimeError("ATTEMPT_RECORD_MISMATCH: " + str(path))
    return value


def revoke(runtime, expected):
    """Revoke first; only the exact registered unit may then be stopped."""
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    revoked = runtime / "REVOKED.json"
    if revoked.exists():
        _bound_record(revoked, expected)
    else:
        try:
            atomic(revoked, {**expected, "at_utc": now()}, True)
        except FileExistsError:
            _bound_record(revoked, expected)
    errors = []
    for suffix in (".timer", ".service"):
        unit = expected["unit"] + suffix
        result = subprocess.run(["systemctl", "stop", unit],
                                capture_output=True, text=True, timeout=20)
        current = state(unit)
        if is_active(current) or (result.returncode and current["LoadState"] != "not-found"):
            errors.append({"unit": unit, "returncode": result.returncode,
                           "state": current, "stderr": result.stderr[-300:]})
    states = inspect(expected["unit"])
    if errors or any(is_active(item) for item in states.values()):
        raise RuntimeError("ATTEMPT_TIMER_REVOCATION_UNVERIFIED: " + json.dumps(errors))
    receipt = runtime / "timer-revocation.json"
    if receipt.exists():
        _bound_record(receipt, expected)
    else:
        atomic(receipt, {**expected, "at_utc": now(), "states": states,
                         "status": "ATTEMPT_TIMER_REVOKED_AND_INACTIVE"}, True)
    return {"status": "ATTEMPT_TIMER_REVOKED_AND_INACTIVE", "states": states}


def arm(runtime, expected, planned_utc, command, owner, post_install_check=None):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        if any(owner.get(key) != item for key, item in expected.items()):
            raise RuntimeError("TIMER_ARM_OWNER_MISMATCH")
        if owner.get("role") != "MASTER_CONTROLLER_ONLY":
            raise RuntimeError("TIMER_ARM_OWNER_ROLE")
        if (owner.get("planned_utc") != planned_utc or
                type(owner.get("controller_pid")) is not int or
                owner["controller_pid"] <= 0):
            raise RuntimeError("TIMER_ARM_OWNER_PROCESS_OR_PLAN")
        try:
            issued = datetime.fromisoformat(owner["issued_utc"]).timestamp()
        except (KeyError, ValueError, TypeError):
            raise RuntimeError("TIMER_ARM_OWNER_TIME")
        age = datetime.now(timezone.utc).timestamp() - issued
        if not 0 <= age <= 30:
            raise RuntimeError("TIMER_ARM_OWNER_STALE")
        if (runtime / "REVOKED.json").exists() or (runtime / "started.json").exists():
            raise RuntimeError("CANNOT_REARM_REVOKED_OR_STARTED")
        before = inspect(expected["unit"])
        if ((runtime / "arm-intent.json").exists() or
                (runtime / "armed.json").exists() or
                any(is_active(item) for item in before.values())):
            raise RuntimeError("DUPLICATE_ARM_REVOKED")
        atomic(runtime / "arm-intent.json",
               {**expected, "planned_utc": planned_utc,
                "owner_pid": owner.get("controller_pid"), "at_utc": now()}, True)
        calendar = datetime.fromisoformat(planned_utc).astimezone(timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f UTC")
        argv = ["systemd-run", "--unit=" + expected["unit"],
                "--on-calendar=" + calendar,
                "--timer-property=AccuracySec=10ms",
                "--timer-property=RandomizedDelaySec=0",
                "--timer-property=Persistent=false",
                "--property=RuntimeMaxSec=340", *command]
        subprocess.run(argv, check=True, capture_output=True, text=True, timeout=15)
        if post_install_check is not None:
            post_install_check()
        if not is_active(state(expected["unit"] + ".timer")):
            raise RuntimeError("TIMER_NOT_ACTIVE_AFTER_ARM")
        atomic(runtime / "armed.json", {**expected, "at_utc": now(),
               "calendar": calendar, "owner_pid": owner.get("controller_pid")}, True)
        return {"status": "ATTEMPT_TIMER_ARMED", "calendar": calendar}
    except BaseException:
        try:
            revoke(runtime, expected)
        except BaseException as cleanup_error:
            raise RuntimeError("ARM_CLEANUP_UNVERIFIED") from cleanup_error
        raise
