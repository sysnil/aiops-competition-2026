"""Score two proposed controls using real requests and independent safety/trace gates."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import yaml

from evaluate_historical_controls import longest_run, percentile


ROOT = Path("/root/stage5-v3-clean-gate-v2")
CONTROL_EDGES = {"image_asset": "image_asset/frontend-proxy->image-provider",
                 "cart_view": "cart_view/frontend->cart"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def decide_control(ratios, pooled_ratio, failures, minimum_requests,
                   minimum_spans, policy):
    if len(ratios) != 15 or any(x is None for x in ratios):
        return {"A_old": False, "B_persistence_peak": False,
                "E_B_and_window": False, "reason": "INCOMPLETE_MINUTE_SERIES"}
    base = failures == 0 and minimum_requests >= policy[
        "workload_qualification"]["control_successful_requests_each_minute_min"] and \
        minimum_spans >= policy["workload_qualification"][
            "control_usable_edge_trace_ids_each_minute_min"]
    high = [value > 1.25 for value in ratios]
    b = (base and longest_run(high) <= 1 and max(ratios) <= 1.50)
    e = b and sum(high) / 15 <= .10 and pooled_ratio is not None and \
        pooled_ratio <= 1.25
    return {"A_old": base and all(value <= 1.25 for value in ratios),
            "B_persistence_peak": b, "E_B_and_window": e,
            "high_minutes": sum(high), "longest_high_run": longest_run(high),
            "max_ratio": max(ratios), "pooled_ratio": pooled_ratio,
            "failures": failures, "minimum_requests": minimum_requests,
            "minimum_spans": minimum_spans}


def control_rows(path, start):
    bins = [[] for _ in range(25)]
    for line in path.read_text().splitlines():
        if not line:
            continue
        row = json.loads(line)
        stamp = datetime.fromisoformat(row.get("time_utc") or row[
            "scheduled_utc"]).timestamp()
        minute = int((stamp - start) // 60)
        if 0 <= minute < 25:
            bins[minute].append(row)
    return bins


def evaluate_control(path, start, span_counts, policy):
    bins = control_rows(path, start)
    successes = [[row["latency_ms"] for row in bucket if row.get("success") and
                  isinstance(row.get("latency_ms"), (int, float))]
                 for bucket in bins]
    before_p95 = [percentile(bucket, .95) for bucket in successes[:10]]
    baseline = percentile(before_p95, .5) if all(
        x is not None for x in before_p95) else None
    ratios = [percentile(bucket, .95) / baseline if bucket and baseline and
              baseline > 0 else None for bucket in successes[10:]]
    pooled = [value for bucket in successes[10:] for value in bucket]
    pooled_ratio = percentile(pooled, .95) / baseline if pooled and baseline else None
    failures = sum(not row.get("success") for bucket in bins for row in bucket)
    min_requests = min(len(bucket) for bucket in successes)
    min_spans = min(span_counts) if len(span_counts) == 25 else 0
    result = decide_control(ratios, pooled_ratio, failures, min_requests,
                            min_spans, policy)
    result["minute_ratios"] = ratios
    result["before_baseline_p95_ms"] = baseline
    result["source_sha256"] = sha(path)
    return result


def workload_quality(run, analysis):
    pods = analysis.get("pod_identity_by_phase", {}).get("before", {})
    rec = [uid for uid, item in pods.items() if item.get("service") ==
           "recommendation"]
    counts = analysis.get("service_server_trace_ids_by_pod_and_minute", {}).get(
        "recommendation", {})
    rec_ok = len(rec) == 2 and all(len(counts.get(uid, [])) == 25 and
        min(counts[uid][5:10]) >= 30 for uid in rec)
    checkout = analysis.get("call_edge_trace_ids_per_minute", {}).get(
        "checkout->cart", [])
    checkout_ok = len(checkout) == 25 and min(checkout[5:10]) >= 30
    probes = analysis.get("probes", {})
    probe_ok = all(probes.get(phase, {}).get("planned") == planned and
        probes[phase].get("valid_numeric", 0) >= minimum and
        probes[phase].get("max_consecutive_invalid", 99) <= 2
        for phase, planned, minimum in (("before", 120, 108),
                                        ("during", 60, 54),
                                        ("after", 120, 108)))
    client_bins = analysis.get("state_machine_client", {})
    clients_ok = bool(client_bins) and all(row["attempted"] == row["successful"]
        for bins in client_bins.values() for row in bins)
    route = read(run / "route-proof.json")
    route_ok = route.get("status") == "ROUTE_FLOW_PROOF_PASS" and len(
        route.get("rows", [])) == 7
    return {"recommendation_two_pod_before_coverage": rec_ok,
            "checkout_cart_before_coverage": checkout_ok,
            "full_probe_schedule": probe_ok,
            "generated_workflow_without_errors": clients_ok,
            "fresh_route_and_business_packet_proof": route_ok,
            "all_pass": all((rec_ok, checkout_ok, probe_ok, clients_ok,
                             route_ok))}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--cpu-safety", type=Path, required=True)
    parser.add_argument("--trace-v2", type=Path, required=True)
    parser.add_argument("--leak-audit", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=ROOT /
                        "design/clean-gate-v2-proposed.yaml")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    policy = yaml.safe_load(args.policy.read_text())
    if policy["status"] != "PROPOSED_NOT_APPROVED":
        raise ValueError("Only independent proposed policy is allowed")
    analysis = read(args.analysis)
    cpu = read(args.cpu_safety)
    trace = read(args.trace_v2)
    leak = read(args.leak_audit)
    lifecycle = read(args.run / "result.json")
    pipeline = read(args.run / "capture-pipeline/pipeline-result.json")
    schedule_path = args.run / "load_pair/schedule.json"
    schedule = read(schedule_path)
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    controls = {name: evaluate_control(args.run / "load_pair/controls" /
        (name + ".jsonl"), start, analysis[
            "fixed_control_edge_trace_ids_per_minute"].get(edge, []), policy)
        for name, edge in CONTROL_EDGES.items()}
    both_high = [minute for minute in range(15) if all(
        controls[name]["minute_ratios"][minute] is not None and
        controls[name]["minute_ratios"][minute] > 1.25 for name in controls)]
    control_rules = {key: not both_high and all(row[key] for row in
        controls.values()) for key in ("A_old", "B_persistence_peak",
                                      "E_B_and_window")}
    cpu_ok = cpu.get("status") == "CPU_SAFETY_PASS" and \
        cpu.get("recorded_samples") == 50 and \
        cpu.get("samples_sha256") == sha(args.cpu_safety.parent / "samples.jsonl")
    trace_ok = trace.get("status") == "CANDIDATE_TRACE_QUALITY_PASS"
    workload = workload_quality(args.run, analysis)
    source_ok = (lifecycle.get("status") == "CLEAN_LIFECYCLE_PASS" and
        lifecycle.get("fault_injected") is False and
        pipeline.get("status") == "CAPTURE_PIPELINE_PASS" and
        leak.get("status") == "NO_PRIVATE_LEAK_DETECTED" and
        all(read(args.run / (name + "-cleanup.json")).get("status") ==
            "ATTEMPT_PROCESSES_STOPPED" for name in ("master", "k8s-worker3")))
    base_ok = cpu_ok and trace_ok and workload["all_pass"] and source_ok
    value = {"status": "CANDIDATE_V2_EVALUATION_NO_PROTOCOL_APPROVAL",
        "at_utc": datetime.now(timezone.utc).isoformat(), "run_id": args.run.name,
        "controls": controls, "simultaneous_high_minutes": both_high,
        "control_rules": control_rules,
        "cpu_safety_pass": cpu_ok, "trace_v2_quality_pass": trace_ok,
        "workload_quality": workload, "lifecycle_source_and_leak_pass": source_ok,
        "B_overall": base_ok and control_rules["B_persistence_peak"],
        "E_overall": base_ok and control_rules["E_B_and_window"],
        "old_control_A_recomputed": control_rules["A_old"],
        "candidate_approved": False, "formal_admission_replaced": False,
        "source_sha256": {str(path): sha(path) for path in
            (args.policy, args.analysis, args.cpu_safety, args.trace_v2,
             args.leak_audit, args.run / "result.json",
             schedule_path)}}
    with args.output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "B_overall":
        value["B_overall"], "E_overall": value["E_overall"],
        "old_control_A": value["old_control_A_recomputed"]}))


if __name__ == "__main__":
    main()
