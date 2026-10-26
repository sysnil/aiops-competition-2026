"""Draft immutable supplement that waits for the first capture before reading."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


OLD = Path("/root/ops-competition-2026/low-stable-v4-20261017/capture/schedule_trace_completion.py")


def span_keys(root):
    result = set()
    for path in sorted((root / "trace-storage-pages").glob("page-*.json")):
        page = json.loads(path.read_text())
        for hit in page["hits"]["hits"]:
            span = hit["_source"]
            key = (span["traceID"], span["spanID"])
            if key in result:
                raise ValueError("Duplicate trace/span identity in a storage snapshot")
            result.add(key)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--first-capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    end = datetime.fromisoformat(args.end).timestamp()
    time.sleep(max(0, end + 60 - time.time()))
    first_manifest = args.first_capture / "capture-manifest.json"
    while not first_manifest.is_file() and time.time() <= end + 90:
        time.sleep(0.5)
    if not first_manifest.is_file():
        args.output.mkdir(parents=True)
        (args.output / "terminal.json").write_text(json.dumps({
            "status": "CAPTURE_INCOMPLETE", "reason": "first_capture_not_frozen_by_plus_90",
            "first_capture": str(args.first_capture), "fault_injected": False}, indent=2) + "\n")
        raise ValueError("First capture did not freeze before +90 seconds")
    first_hash = hashlib.sha256(first_manifest.read_bytes()).hexdigest()
    started = datetime.now(timezone.utc).isoformat()
    subprocess.run(["python3", str(OLD), "--phase", args.phase, "--start",
                    args.start, "--end", args.end, "--first-capture",
                    str(args.first_capture), "--output", str(args.output)],
                   check=True, timeout=120)
    if hashlib.sha256(first_manifest.read_bytes()).hexdigest() != first_hash:
        raise ValueError("First capture changed during supplement")
    first_keys = span_keys(args.first_capture)
    later_keys = span_keys(args.output)
    missing = first_keys - later_keys
    audit = {"status": "IDENTITY_PASS" if not missing else "IDENTITY_FAIL",
             "phase": args.phase, "first_count": len(first_keys),
             "supplement_count": len(later_keys), "new_count": len(later_keys - first_keys),
             "lost_first_count": len(missing), "first_manifest_sha256": first_hash,
             "supplement_start_utc": started,
             "query_window_equal": json.loads((args.output / "trace-storage-source.json").read_text())[
                 "query_start_utc"] == json.loads(first_manifest.read_text())["window"][
                     "start_utc"]}
    (args.output / "identity-audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    if audit["status"] != "IDENTITY_PASS" or not audit["query_window_equal"]:
        raise ValueError("Supplement lost identity or changed query window")
    print(json.dumps(audit), flush=True)


if __name__ == "__main__":
    main()
