#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Export only the organizer-test token as a protected environment file")
    parser.add_argument("--credentials", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.credentials.read_text(encoding="utf-8"))
    record = next(item for item in payload["credentials"] if item["team_id"] == "ORGANIZER-TEST")
    args.output.write_text(f"AIOPS_TEAM_TOKEN={record['token']}\n", encoding="utf-8")
    args.output.chmod(0o600)
    print(json.dumps({"team_id": record["team_id"], "token_id": record["token_id"], "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
