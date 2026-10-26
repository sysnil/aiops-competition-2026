"""Independent draft live capture scheduler for no-fault calibration windows."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys


OLD = Path("/root/stage5-v3-autonomous-preparation/scripts")
NEW = Path("/root/stage5-v3-clean-gate-v2/scripts")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    start = datetime.fromisoformat(args.start.replace("Z", "+00:00"))
    if start.tzinfo is None or args.output.exists():
        raise ValueError("Need a new output and explicit UTC start")
    args.output.mkdir(parents=True)
    processes = {}
    for phase, lo, hi in (("before", 0, 600), ("during", 600, 900),
                          ("after", 900, 1500)):
        begin = (start + timedelta(seconds=lo)).isoformat()
        end = (start + timedelta(seconds=hi)).isoformat()
        first = args.output / "captures" / phase
        commands = {
            "first-" + phase: [sys.executable, OLD / "capture_live_c2.py",
                "--phase", phase, "--start-utc", begin, "--end-utc", end,
                "--trace-source", "JAEGER_OPENSEARCH_STORAGE", "--wait-until-end",
                "--output", first],
            "completion-" + phase: [sys.executable, NEW / "schedule_trace_completion_v2.py",
                "--phase", phase, "--start", begin, "--end", end,
                "--first-capture", first, "--output",
                args.output / "trace-completion" / phase],
            "lag-" + phase: [sys.executable, OLD / "check_ingest_lag.py",
                "--start", begin, "--end", end,
                "--output", args.output / "ingest-lag" / (phase + ".json")],
            "final-and-audit-" + phase: [sys.executable, NEW /
                "schedule_trace_v2.py", "--phase", phase, "--start", begin,
                "--end", end, "--first", first, "--plus60",
                args.output / "trace-completion" / phase,
                "--output", args.output / "trace-final" / phase,
                "--audit-output", args.output / "trace-late-audit" / phase],
        }
        for name, command in commands.items():
            out = (args.output / (name + ".stdout")).open("x")
            err = (args.output / (name + ".stderr")).open("x")
            processes[name] = (subprocess.Popen([str(x) for x in command],
                               stdout=out, stderr=err), out, err)
    codes = {}
    try:
        for name, (process, out, err) in processes.items():
            codes[name] = process.wait(timeout=1800)
            out.close()
            err.close()
    finally:
        for process, out, err in processes.values():
            if process.poll() is None:
                process.terminate()
            if not out.closed:
                out.close()
            if not err.closed:
                err.close()
    result = {"status": "CAPTURE_PIPELINE_PASS" if len(codes) == 12 and
              all(code == 0 for code in codes.values()) else "CAPTURE_PIPELINE_FAILED",
              "started_from_utc": start.isoformat(),
              "completed_at_utc": datetime.now(timezone.utc).isoformat(),
              "process_exit_codes": codes, "trace_source": "JAEGER_OPENSEARCH_STORAGE",
              "scheduler_revision": "independent_cgv2_first_plus60_plus120_audit240"}
    (args.output / "pipeline-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    if result["status"] != "CAPTURE_PIPELINE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
