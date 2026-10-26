#!/usr/bin/env python3
"""Capture, mirror and verify the one frozen 45-minute raw Trace study."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("/root/stage5-v3-collector-reliability")
RUN = ROOT / "runs/CR-CONTINUOUS-45M-20261012"
OUT = ROOT / "archive/continuous-archive-status.json"
DATA = "/data/rca-archives/stage5-v3-collector-reliability-20261012"
W2 = ("192.168.53.54", "/tmp/stage5v3-w2-archive.sock")
W3 = ("192.168.53.55", "/tmp/stage5v3-w3-archive.sock")
STAGING = "/tmp/s5v3-collector-reliability"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ssh(worker, *command, timeout=900):
    ip, socket = worker
    return subprocess.check_output(["ssh", "-o", "BatchMode=yes",
        "-o", "ControlPath=" + socket, "root@" + ip, *command],
        text=True, timeout=timeout)


def rsync_to(worker, source, target):
    ip, socket = worker
    subprocess.run(["rsync", "-a", "-e", "ssh -o ControlPath=" + socket,
        str(source), "root@" + ip + ":" + target], check=True, timeout=180)


def rsync_from(worker, source, target):
    ip, socket = worker
    subprocess.run(["rsync", "-a", "-e", "ssh -o ControlPath=" + socket,
        "root@" + ip + ":" + source, str(target)], check=True, timeout=180)


def stream_tree():
    check = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o",
        "ControlPath=" + W3[1], "root@" + W3[0], "test", "!", "-e",
        DATA + "/continuous-primary"], capture_output=True, timeout=15)
    if check.returncode != 0:
        raise RuntimeError("Worker3 archive destination already exists")
    source = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o",
        "ControlPath=" + W2[1], "root@" + W2[0], "tar", "-C", DATA,
        "-cf", "-", "continuous-primary"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    try:
        target = subprocess.Popen(["ssh", "-o", "BatchMode=yes", "-o",
            "ControlPath=" + W3[1], "root@" + W3[0], "tar", "-C", DATA,
            "-xf", "-"], stdin=source.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        source.stdout.close()
        target_out, target_err = target.communicate(timeout=300)
        source_err = source.stderr.read()
        source_code = source.wait(timeout=30)
        if target.returncode or source_code:
            raise RuntimeError("Worker stream copy failed: " + str(
                (source_err + target_err)[-300:]))
    finally:
        if source.poll() is None:
            source.terminate()


def main():
    if OUT.exists():
        raise ValueError("Refusing overwrite")
    output = {"started_utc": datetime.now(timezone.utc).isoformat(),
        "run": RUN.name, "source_code_sha256": sha(Path(__file__)),
        "fault_injected": False, "stages": []}
    try:
        workload = json.loads((RUN / "result.json").read_text())
        if workload["status"] not in ("CONTINUOUS_WORKLOAD_COMPLETED",
                "CONTINUOUS_DIAGNOSTIC_WORKLOAD_COMPLETED"):
            raise ValueError("Continuous workload did not finish safely")
        ssh(W2, "mkdir", "-p", DATA + "/continuous-primary/issued")
        ssh(W3, "mkdir", "-p", DATA)
        rsync_to(W2, str(RUN) + "/", DATA + "/continuous-primary/issued/")
        output["stages"].append("ISSUED_REQUESTS_TRANSFERRED_TO_WORKER2")
        source = STAGING + "/capture_continuous_traces.py"
        for stage, directory in (("first", "trace-120"), ("late", "trace-240")):
            response = ssh(W2, "python3", "-B", source,
                "--run", DATA + "/continuous-primary/issued",
                "--stage", stage, "--output", DATA + "/continuous-primary/" + directory,
                timeout=900)
            output["stages"].append({stage: response.strip()[-500:]})
        comparison_failed = False
        try:
            response = ssh(W2, "python3", "-B", STAGING + "/compare_continuous_traces.py",
                "--first", DATA + "/continuous-primary/trace-120",
                "--late", DATA + "/continuous-primary/trace-240",
                "--output", DATA + "/continuous-primary/comparison.json", timeout=900)
            output["stages"].append({"comparison": response.strip()[-500:]})
        except subprocess.CalledProcessError as error:
            comparison_failed = True
            output["stages"].append({"comparison_failed_but_raw_preserved": str(error)})
        stream_tree()
        output["stages"].append("RAW_TREE_STREAMED_TO_WORKER3")
        ssh(W2, "python3", "-B", STAGING + "/archive_continuous.py", "create", timeout=900)
        source_manifest = DATA + "/continuous-source-manifest.json"
        local_manifest = ROOT / "archive/continuous-source-manifest.json"
        rsync_from(W2, source_manifest, local_manifest)
        rsync_to(W3, local_manifest, source_manifest)
        proofs = {}
        for worker, label in ((W2, "worker2"), (W3, "worker3")):
            remote = STAGING + "/continuous-" + label + "-proof.json"
            ssh(worker, "python3", "-B", STAGING + "/archive_continuous.py",
                "verify", "--output", remote, timeout=900)
            local = ROOT / "archive" / ("continuous-" + label + "-proof.json")
            rsync_from(worker, remote, local)
            proofs[label] = json.loads(local.read_text())
        if ({row["worker_ip"] for row in proofs.values()} != {W2[0], W3[0]} or
                len({row["source_manifest_sha256"] for row in proofs.values()}) != 1 or
                next(iter(proofs.values()))["source_manifest_sha256"] != sha(local_manifest)):
            raise RuntimeError("Dual Worker source manifest mismatch")
        for relative, name in (("trace-120/capture-manifest.json", "continuous-trace-120-manifest.json"),
            ("trace-240/capture-manifest.json", "continuous-trace-240-manifest.json"),
            ("comparison.json", "continuous-comparison.json")):
            target = ROOT / "results" / name
            rsync_from(W2, DATA + "/continuous-primary/" + relative, target)
        output.update({"status": "CONTINUOUS_RAW_DUAL_ARCHIVE_VERIFIED_WITH_COMPARISON_FAILURE"
            if comparison_failed else "CONTINUOUS_RAW_DUAL_ARCHIVE_VERIFIED",
            "source_manifest_sha256": sha(local_manifest),
            "worker_archive_paths": [row["archive_path"] for row in proofs.values()],
            "files": next(iter(proofs.values()))["file_count"],
            "bytes": next(iter(proofs.values()))["total_bytes"]})
    except Exception as error:
        output["status"] = "CONTINUOUS_ARCHIVE_INCOMPLETE"
        output["error"] = str(error)
        raise
    finally:
        output["completed_utc"] = datetime.now(timezone.utc).isoformat()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(output, indent=2) + "\n")
        print(json.dumps({key: output.get(key) for key in ("status", "files", "bytes", "error")}))


if __name__ == "__main__":
    main()
