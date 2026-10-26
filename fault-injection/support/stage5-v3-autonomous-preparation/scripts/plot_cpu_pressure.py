#!/usr/bin/env python3
"""Plot captured Node CPU against the separate pre-run workload ceiling."""

import argparse
from datetime import datetime
import json
from pathlib import Path

import plotly.graph_objects as go


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--resource-decision", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("CPU curve output must be new")
    schedule = json.loads((args.run / "load_pair/schedule.json").read_text())
    resource = json.loads(args.resource_decision.read_text())
    if resource["run_id"] != schedule["run_id"]:
        raise ValueError("CPU reference belongs to a different run")
    start = datetime.fromisoformat(schedule["scheduled_start_utc"]).timestamp()
    by_node = {}
    for phase in ("before", "during", "after"):
        matrix = json.loads((args.run / "capture-pipeline/captures" / phase /
                             "metrics/node_cpu.json").read_text())
        for series in matrix["data"]["result"]:
            node = series["metric"].get("k8s_node_name")
            if node:
                by_node.setdefault(node, []).extend(
                    ((float(stamp) - start) / 60, float(value))
                    for stamp, value in series["values"])
    figure = go.Figure()
    colors = {"k8s-worker1": "#4B7B9B", "k8s-worker2": "#789469",
              "k8s-worker3": "#D15B3C", "k8s-master": "#868C90"}
    for node, values in sorted(by_node.items()):
        figure.add_trace(go.Scatter(x=[point[0] for point in values],
            y=[point[1] for point in values], mode="lines", name=node,
            line={"color": colors.get(node, "#555555"),
                  "width": 3 if node == "k8s-worker3" else 1.5}))
    baseline = resource["median_prerun_cores"]["k8s-worker3"]
    ceiling = baseline + resource["threshold_added_cores_max"]
    figure.add_hline(y=baseline, line_dash="dot", line_color="#7A8085",
                     annotation_text="Worker3 pre-run median")
    figure.add_hline(y=ceiling, line_dash="dash", line_color="#A13737",
                     annotation_text="Worker3 +0.25 core ceiling")
    for minute in (10, 15):
        figure.add_vline(x=minute, line_dash="dot", line_color="#7A8085")
    figure.update_layout(title=schedule["run_id"] + " | Captured Node CPU",
        xaxis_title="Minute from start (0-9 Before, 10-14 During, 15-24 After)",
        yaxis_title="CPU cores", template="plotly_white", height=590,
        hovermode="x unified", margin={"l": 70, "r": 30, "t": 75, "b": 65})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(args.output), include_plotlyjs=True, full_html=True)
    print(json.dumps({"status": "PLOT_WRITTEN", "path": str(args.output),
                      "worker3_added_cores": resource["added_cores"]["k8s-worker3"]}))


if __name__ == "__main__":
    main()
