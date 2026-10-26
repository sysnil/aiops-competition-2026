"""Versioned pre-production authorization decision for one bound action.

All writers of permit/revocation state and the signer take the same lock.
This closes the pre-sign revoke race; an already signed short grant still has
the protocol's bounded lifetime and must be checked at the effect boundary.
"""

import fcntl
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path


def read_bound(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("ACTION_STATE_MISSING_OR_SYMLINK")
    return json.loads(path.read_text())


@contextmanager
def decision_lock(directory):
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("ACTION_DIRECTORY_INVALID")
    path = directory / "decision.lock"
    if path.is_symlink():
        raise ValueError("ACTION_LOCK_SYMLINK")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def create_once(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, (json.dumps(value, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def revoke(directory, identity, reason):
    """Idempotent only for the same Attempt/Action/Manifest identity."""
    directory = Path(directory)
    state = read_bound(directory / "state.json")
    for key in ("run_id", "action_id", "manifest_sha256"):
        if state.get(key) != identity.get(key):
            raise ValueError("ACTION_REVOKE_IDENTITY_" + key)
    intent = directory / "REVOCATION_REQUESTED.json"
    if intent.exists():
        previous = read_bound(intent)
        if any(previous.get(k) != identity[k] for k in identity):
            raise ValueError("ACTION_REVOKE_INTENT_CONFLICT")
    else:
        try:
            create_once(intent, {**identity, "status": "REVOCATION_REQUESTED",
                                 "at_epoch": time.time()})
        except FileExistsError:
            previous = read_bound(intent)
            if any(previous.get(k) != identity[k] for k in identity):
                raise ValueError("ACTION_REVOKE_INTENT_CONFLICT")
    with decision_lock(directory) as root:
        state = read_bound(root / "state.json")
        for key in ("run_id", "action_id", "manifest_sha256"):
            if state.get(key) != identity.get(key):
                raise ValueError("ACTION_REVOKE_IDENTITY_" + key)
        path = root / "REVOKED.json"
        if path.exists():
            previous = read_bound(path)
            if any(previous.get(k) != identity[k] for k in identity):
                raise ValueError("ACTION_REVOKE_CONFLICT")
            return previous
        record = {**identity, "status": "REVOKED", "reason": reason,
                  "at_epoch": time.time()}
        create_once(path, record)
        return record


def permit(directory, identity, value):
    with decision_lock(directory) as root:
        state = read_bound(root / "state.json")
        if any(state.get(k) != identity.get(k) for k in identity):
            raise ValueError("ACTION_PERMIT_IDENTITY")
        if ((root / "REVOKED.json").exists() or
                (root / "REVOCATION_REQUESTED.json").exists() or
                (root / "consumed.json").exists()):
            raise ValueError("ACTION_PERMIT_TERMINAL")
        if any(value.get(k) != identity.get(k) for k in identity):
            raise ValueError("ACTION_PERMIT_VALUE_IDENTITY")
        create_once(root / "permit.json", value)
        return value
