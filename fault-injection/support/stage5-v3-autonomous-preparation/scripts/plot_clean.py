#!/usr/bin/env python3
"""Plot immutable no-fault qualification metrics without changing source data."""

import argparse
import json
from pathlib import Path

from plotly.subplots import make_subplots
import plotly.graph_objects as go


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Refusing overwrite of a qualification chart")
    value = json.loads(args.analysis.read_text())
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True,
        subplot_titles=("Recommendation business Server Trace IDs / minute by Pod UID",
                        "Ordinary business control client p95 / clean median",
                        "Ordinary business control failed requests / minute"),
        vertical_spacing=0.09)
    colors = ["#007F86", "#D15B3C", "#6847A4", "#63754C"]
    for index, (pod, counts) in enumerate(sorted(value[
            "service_server_trace_ids_by_pod_and_minute"].get(
                "recommendation", {}).items())):
        figure.add_trace(go.Scatter(x=list(range(25)), y=counts,
            mode="lines+markers", name=pod, legendgroup=pod,
            line={"color": colors[index]}), row=1, col=1)
    figure.add_hline(y=30, line_dash="dash", line_color="#9A3636", row=1, col=1)
    for index, (name, rows) in enumerate(sorted(value["fixed_control_client"].items())):
        x = [row["minute"] for row in rows]
        figure.add_trace(go.Scatter(x=x,
            y=[row["client_p95_to_clean_median"] for row in rows],
            mode="lines+markers", name=name + " p95 ratio", legendgroup=name,
            line={"color": colors[index]}), row=2, col=1)
        figure.add_trace(go.Bar(x=x,
            y=[row["attempted"] - row["successful"] for row in rows],
            name=name + " failed", legendgroup=name,
            marker_color=colors[index]), row=3, col=1)
    figure.add_hline(y=1.25, line_dash="dash", line_color="#9A3636", row=2, col=1)
    for boundary in (10, 15):
        figure.add_vline(x=boundary - 0.5, line_dash="dot", line_color="#67737B",
                         row="all", col=1)
    figure.update_layout(title=value["run_id"] + " | No-fault qualification",
                         template="plotly_white", height=980,
                         barmode="group", hovermode="x unified",
                         margin={"l": 70, "r": 30, "t": 90, "b": 65})
    figure.update_xaxes(title_text="Minute from window start (0-9 Before, 10-14 During, 15-24 After)",
                        row=3, col=1)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(args.output), include_plotlyjs=True, full_html=True)
    print(json.dumps({"status": "PLOT_WRITTEN", "path": str(args.output)}))


if __name__ == "__main__":
    main()
