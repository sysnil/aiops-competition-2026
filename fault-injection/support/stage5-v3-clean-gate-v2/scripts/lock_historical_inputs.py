"""Hash read-only exploratory inputs without changing historical outcomes."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SOURCES = {
    "historical-control-statistics-v2.json": "stage5-v3-baseline-calibration/results/historical-control-statistics-v2.json",
    "S5V3AP-CAL-D3-control-statistics.json": "stage5-v3-baseline-calibration/results/S5V3AP-CAL-D3-control-statistics.json",
    "abc-cpu-attribution-v2.json": "stage5-v3-baseline-calibration/results/abc-cpu-attribution-v2.json",
    "S5V3AP-CAL-D3-worker3-cpu-offline.json": "stage5-v3-baseline-calibration/results/S5V3AP-CAL-D3-worker3-cpu-offline.json",
    "historical-prerun-reference-audit.json": "stage5-v3-baseline-calibration/results/historical-prerun-reference-audit.json",
    "S5V3AP-VAL-1-control-statistics.json": "stage5-v3-storage-migration-and-resume/results/S5V3AP-VAL-1-control-statistics.json",
    "S5V3AP-VAL-2-control-statistics.json": "stage5-v3-storage-migration-and-resume/results/S5V3AP-VAL-2-control-statistics.json",
    "VAL-1-resource-decision.json": "stage5-v3-storage-migration-and-resume/results/S5V3AP-VAL-1-resource-pressure/decision.json",
    "VAL-2-resource-decision.json": "stage5-v3-storage-migration-and-resume/results/S5V3AP-VAL-2-resource-pressure/decision.json",
    "d3-late-span-id-audit-v3.json": "stage5-v3-storage-migration-and-resume/results/d3-late-span-id-audit-v3.json",
    "d3-late-uid-crosscheck.json": "stage5-v3-storage-migration-and-resume/results/d3-late-uid-crosscheck.json",
}
REPORTS = (
    "stage5-v3-storage-migration-and-resume/reports/final-report.md",
    "stage5-v3-baseline-calibration/reports/baseline-calibration-report.md",
    "stage5-v3-baseline-calibration/reports/control-noise-analysis.md",
    "stage5-v3-baseline-calibration/reports/worker3-cpu-attribution.md",
    "stage5-v3-baseline-calibration/reports/trace-capture-timing-audit.md",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    output = ROOT / "snapshots/historical-input-manifest.json"
    if output.exists():
        raise ValueError("Refusing overwrite")
    rows = {}
    for name, original in SOURCES.items():
        copy = ROOT / "snapshots/reference" / name
        source = WORKSPACE / original
        if sha(copy) != sha(source):
            raise ValueError("Reference copy differs: " + name)
        rows[name] = {"source": str(source), "copy": str(copy),
                      "sha256": sha(copy)}
    reports = {str(WORKSPACE / name): sha(WORKSPACE / name)
               for name in REPORTS}
    value = {"status": "EXPLORATORY_HISTORICAL_INPUTS_LOCKED", "at_utc":
        datetime.now(timezone.utc).isoformat(), "files": rows,
        "reports_sha256": reports,
        "historic_results_reclassified": False,
        "stage5_v2_test_accessed": False}
    with output.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": value["status"], "files": len(rows),
                      "reports": len(reports), "manifest_sha256": sha(output)}))


if __name__ == "__main__":
    main()
