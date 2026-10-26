"""Render static request-level diagnostic figures from audited JSON evidence."""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def time_minute(value, start):
    return (datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() -
            start) / 60


def save(fig, output):
    fig.tight_layout()
    fig.savefig(output, dpi=190, bbox_inches="tight")


def plot_requests(plt, run, name, output):
    start = datetime.fromisoformat(run["start_utc"]).timestamp()
    rows = [row for row in run["selected_requests"] if row["control"] == name]
    baseline = run["control_summary"][name]["baseline_median_minute_p95_ms"]
    fig, ax = plt.subplots(figsize=(13, 5))
    colors = {"6305d17a-27f0-4dbf-8fc7-7238acb3be91": "#d04d34",
              "d4bb49b4-6fb8-4a07-aaf6-e2caff5df974": "#177f80"}
    if name == "cart_view":
        for uid, color in colors.items():
            selected = [row for row in rows if row["frontend_server_spans"]
                        and row["frontend_server_spans"][0]["pod_uid"] == uid]
            ax.scatter([time_minute(row["completion_utc"], start) for row in selected],
                       [row["latency_ms"] for row in selected], s=13,
                       alpha=.55, color=color, label="Frontend " + (
                           "Worker1" if color == "#d04d34" else "Worker2"))
    else:
        ax.scatter([time_minute(row["completion_utc"], start) for row in rows],
                   [row["latency_ms"] for row in rows], s=14, alpha=.6,
                   color="#177f80", label="Client request")
    minute_rows = run["control_summary"][name]["minutes"]
    ax.plot([minute + .5 for minute in range(8, 15)],
            [minute_rows[minute]["p95"] for minute in range(8, 15)],
            color="#222b35", linewidth=2, marker="o", markersize=4,
            label="Minute p95")
    ax.axhline(baseline, linestyle="--", linewidth=1, color="#55718a",
               label=f"Before reference p95 {baseline:.3f} ms")
    ax.axhline(baseline * 1.25, linestyle=":", linewidth=1, color="#c77721",
               label="Old 1.25x limit")
    ax.axvspan(10, 15, color="#eaf0f2", zorder=-1)
    ax.set(xlim=(8, 15), xlabel="Minutes since scheduled start (completion time)",
           ylabel="Client response time (ms)",
           title=f"VAL-1 {name}: every request and minute p95")
    ax.grid(alpha=.18)
    ax.legend(loc="upper right", fontsize=8)
    save(fig, output)
    plt.close(fig)


def plot_comparison(plt, runs, output):
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for ax, name in zip(axes, ("image_asset", "cart_view")):
        for run, color in zip(runs, ("#d04d34", "#177f80")):
            ratios = [row["p95_ratio"] for row in run[
                "control_summary"][name]["minutes"]]
            ax.plot([i + .5 for i in range(25)], ratios, marker="o",
                    markersize=3, linewidth=1.5, color=color,
                    label=run["run_id"][-5:])
        ax.axhline(1.25, linestyle="--", linewidth=1, color="#c77721",
                   label="Old limit 1.25x")
        ax.axhline(1.50, linestyle=":", linewidth=1, color="#944b47",
                   label="Candidate B single-minute ceiling 1.50x")
        ax.axvspan(10, 15, color="#eaf0f2", zorder=-1)
        ax.set(ylabel="Minute p95 / Before reference", title=name)
        ax.grid(alpha=.18)
        ax.legend(fontsize=8, ncol=2)
    axes[-1].set(xlabel="Minutes since scheduled start")
    save(fig, output)
    plt.close(fig)


def plot_resource(plt, run, samples, output):
    start = datetime.fromisoformat(run["start_utc"]).timestamp()
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    for node, color in (("k8s-worker1", "#d04d34"),
                        ("k8s-worker2", "#177f80")):
        x = [time_minute(row["planned_utc"], start) for row in samples]
        axes[0].plot(x, [row["nodes"][node]["cpu_cores"] for row in samples],
                     color=color, marker=".", label=node)
        axes[1].plot(x, [row["nodes"][node]["psi_some_avg60_percent"]
                         for row in samples], color=color, marker=".", label=node)
    for ax in axes:
        ax.axvspan(10, 12, color="#f9e7de", zorder=-1)
        ax.axvspan(12, 13, color="#e6f1f0", zorder=-1)
        ax.set(xlim=(8, 14.5))
        ax.grid(alpha=.18)
        ax.legend(fontsize=8)
    axes[0].set(ylabel="Node CPU usage (cores)",
                title="VAL-1 resource activity near Cart and Image excursions")
    axes[1].set(xlabel="Minutes since scheduled start",
                ylabel="CPU PSI some avg60 (%)")
    save(fig, output)
    plt.close(fig)


def plot_layers(plt, run, output):
    rows = [row for row in run["selected_requests"] if row[
        "control"] == "cart_view" and row["completion_minute"] in (10, 11)]
    rows = sorted(rows, key=lambda row: row["latency_ms"], reverse=True)[:20]
    rows = list(reversed(rows))
    fig, ax = plt.subplots(figsize=(12, 7))
    x = list(range(len(rows)))
    values = (("Client", [row["latency_ms"] for row in rows], "#243d55"),
              ("Envoy total", [row["proxy_access"][0].get("proxy_duration_ms")
                               for row in rows], "#d04d34"),
              ("Frontend server", [row["frontend_server_max_duration_ms"]
                                   for row in rows], "#177f80"),
              ("Cart server", [row["target_server_max_duration_ms"]
                               for row in rows], "#c77721"))
    for index, (label, numbers, color) in enumerate(values):
        ax.barh([i + (index - 1.5) * .18 for i in x], numbers, height=.17,
                color=color, label=label)
    ax.set(yticks=x, yticklabels=[row["trace_id"][:8] for row in rows],
           xlabel="Reported duration (ms), each span uses its own clock",
           ylabel="Trace ID prefix", title="VAL-1 Cart: 20 slowest client requests")
    ax.grid(axis="x", alpha=.18)
    ax.legend(ncol=4, loc="lower right", fontsize=8)
    save(fig, output)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis", type=Path, required=True)
    parser.add_argument("--cpu", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError("Refusing overwrite of figure directory")
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10,
                         "axes.spines.top": False, "axes.spines.right": False})
    result = read(args.analysis)
    samples = [json.loads(line) for line in args.cpu.read_text().splitlines()
               if line]
    args.output_dir.mkdir(parents=True)
    runs = result["runs"]
    plot_requests(plt, runs[0], "image_asset", args.output_dir /
                  "val1-image-requests.png")
    plot_requests(plt, runs[0], "cart_view", args.output_dir /
                  "val1-cart-requests-by-frontend.png")
    plot_comparison(plt, runs, args.output_dir / "minute-p95-comparison.png")
    plot_resource(plt, runs[0], samples, args.output_dir /
                  "val1-worker-resource-context.png")
    plot_layers(plt, runs[0], args.output_dir /
                "val1-cart-trace-layer-durations.png")
    with (args.output_dir / "figure-sources.json").open("x") as stream:
        json.dump({"analysis_sha256": sha(args.analysis), "cpu_sha256": sha(args.cpu),
                   "plot_code_sha256": sha(Path(__file__)),
                   "figures": sorted(path.name for path in
                                     args.output_dir.glob("*.png"))}, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": "FIGURES_CREATED", "count": 5}))


if __name__ == "__main__":
    main()
