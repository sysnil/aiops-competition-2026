"""Read-only, short-window fault sensitivity summary from Phase 4 evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


RUNS = ("P4-W3W2-S1", "P4-W3W2-S2")
KINDS = ("control-analysis", "target-analysis", "probe-analysis")


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(root):
    runs = []
    sources = {}
    for run in RUNS:
        files = {kind: root / (run + "-" + kind + "-summary.json")
                 for kind in KINDS}
        sources.update({str(path): sha(path) for path in files.values()})
        controls = read(files["control-analysis"])
        target = read(files["target-analysis"])
        probe = read(files["probe-analysis"])
        if target["run_id"] != run or probe["path"] != \
                "NETWORK_PATH/k8s-worker3/k8s-worker2":
            raise ValueError("Unregistered Phase 4 evidence")
        during = [row for row in target["minutes"] if row["phase"] == "during"]
        before = [row for row in target["minutes"] if row["phase"] == "before"]
        if len(during) != 1 or len(before) != 2:
            raise ValueError("Expected two-before, one-during Phase 4 window")
        reference = sorted(row["p95_checkout_cart_client_ms"] for row in before)
        before_median = sum(reference) / 2
        target_ratio = during[0]["p95_checkout_cart_client_ms"] / before_median
        normal = {name: {"during_before_p95_ratio": row["during_before_ratio"],
                          "minimum_requests_met": row["all_five_request_bins_pass"],
                          "minimum_edge_spans_met": row["all_five_trace_bins_pass"]}
                  for name, row in controls["stability"].items()
                  if name in ("image_asset", "cart_view")}
        affected = controls["stability"]["product_detail"]
        path = probe["phases"]["during"]
        runs.append({"run_id": run, "window_minutes": 5,
            "formal_B_or_E_15_minute_decision": "NOT_COMPUTABLE_SHORT_WINDOW",
            "network_path": probe["path"],
            "during_probe_planned": path["planned"],
            "during_probe_valid": path["valid_iperf"],
            "during_probe_timeout": path["timeout"],
            "during_loss_fraction": path["median_udp_loss_fraction"],
            "affected_checkout_cart_trace_ids": during[0][
                "verified_call_edge_trace_ids"],
            "affected_checkout_cart_p95_ratio": target_ratio,
            "eligible_independent_controls": normal,
            "affected_product_detail_ratio": affected["during_before_ratio"],
            "affected_product_detail_role": "AFFECTED_NOT_NORMAL_CONTROL",
            "affected_edge_above_B_single_minute_ceiling": affected[
                "during_before_ratio"] > 1.5,
            "normal_controls_below_old_1_25_during": all(row[
                "during_before_p95_ratio"] <= 1.25 for row in normal.values())})
    return {"status": "READ_ONLY_SHORT_WINDOW_SENSITIVITY_REPLAY",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "source_sha256": sources, "runs": runs,
        "new_faults_injected": 0,
        "candidate_B_E_approved": False,
        "limitation": "Phase 4 has 2+1+2 minutes, not the locked 10+5+10 window; "
                      "a full B/E decision and real degraded eligible-control "
                      "sensitivity cannot be inferred."}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite")
    result = summarize(args.reference_root)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "runs": len(result["runs"])}))


if __name__ == "__main__":
    main()
