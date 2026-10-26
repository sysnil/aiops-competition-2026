"""Refuse any real rehearsal unless a fresh independent safety decision passes."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


def authorize(decision, now=None):
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    from global_safety import parse_time
    stamp = parse_time(decision.get("captured_at_utc"))
    return (decision.get("status") == "GLOBAL_SAFETY_PASS" and
            decision.get("faults_allowed") is True and
            decision.get("checks") and all(decision["checks"].values()) and
            stamp is not None and 0 <= now - stamp <= 180)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", type=Path, required=True)
    parser.add_argument("--real", action="store_true")
    args = parser.parse_args()
    decision = json.loads(args.decision.read_text())
    if args.real and not authorize(decision):
        raise SystemExit("REFUSED_REAL_REHEARSAL: Global Safety is not fresh PASS")
    print("SYNTHETIC_DRY_RUN" if not args.real else "SAFETY_PREFLIGHT_ONLY")


if __name__ == "__main__":
    main()
