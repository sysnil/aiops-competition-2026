#!/usr/bin/env python3
"""Render actual NetworkPath probe samples, preserving missing values as gaps."""

import argparse
import json
from pathlib import Path

from plotly.subplots import make_subplots
import plotly.graph_objects as go


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Probe curve output must be new")
    schedule = json.loads((args.run / "load_pair/schedule.json").read_text())
    rows = []
    for phase in ("before", "during", "after"):
        path = args.run / ("probe-" + phase) / "observations.jsonl"
        rows.extend({**json.loads(line), "phase": phase} for line in
                    path.read_text().splitlines() if line)
    rows.sort(key=lambda row: row["scheduled_utc"])
    figure = make_subplots(rows=3, cols=1, shared_xaxes=True,
        subplot_titles=("Received throughput (Mbps)", "Receiver loss (%)",
                        "ICMP median RTT (ms)"), vertical_spacing=0.09)
    x = [row["scheduled_utc"] for row in rows]
    y = [row["iperf3"].get("throughput_bps") for row in rows]
    figure.add_trace(go.Scatter(x=x, y=[v/1e6 if v is not None else None for v in y],
        name="throughput", mode="lines+markers", line={"color": "#007F86"},
        connectgaps=False), row=1, col=1)
    loss = [row["iperf3"].get("loss_fraction") for row in rows]
    figure.add_trace(go.Scatter(x=x, y=[100*v if v is not None else None for v in loss],
        name="receiver loss", mode="lines+markers", line={"color": "#D15B3C"},
        connectgaps=False), row=2, col=1)
    figure.add_trace(go.Scatter(x=x,
        y=[row["icmp"].get("rtt_median_ms") for row in rows],
        name="RTT", mode="lines+markers", line={"color": "#6847A4"},
        connectgaps=False), row=3, col=1)
    for boundary in ("before_end_utc", "during_end_utc"):
        figure.add_vline(x=schedule[boundary], line_dash="dot",
                         line_color="#68757C", row="all", col=1)
    missing = {status: sum(row["status"] == status for row in rows)
               for status in {row["status"] for row in rows}}
    figure.update_layout(title=schedule["run_id"] + " | W3 to W2 scheduled probes",
                         template="plotly_white", height=900,
                         hovermode="x unified", margin={"l": 70, "r": 30,
                                                       "t": 90, "b": 60})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.write_html(str(args.output), include_plotlyjs=True, full_html=True)
    print(json.dumps({"status": "PLOT_WRITTEN", "samples": len(rows),
                      "probe_status_counts": missing, "path": str(args.output)}))


if __name__ == "__main__":
    main()
