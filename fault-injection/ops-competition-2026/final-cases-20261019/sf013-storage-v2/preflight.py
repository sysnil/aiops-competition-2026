"""Read-only case-bound capacity preflight using prior qualified reservations."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sys


HERE = Path(__file__).resolve().parent
OPS = HERE.parents[1]
STORAGE = OPS / "low-stable-v4-20261017/storage-v2"
sys.path.insert(0, str(OPS / "sf003-to-sf012-production-20261015/scripts"))
from dynamic_storage import HOSTS, decide, remote

SOCKETS = {"master": "/tmp/sf-final-master.sock", "worker1": "/tmp/sf-final-w1.sock",
           "worker2": "/tmp/sf-final-w2.sock", "worker3": "/tmp/sf-final-w3.sock",
           "vm1": "/tmp/sf-final-vm1.sock"}
for host, (address, _, data) in list(HOSTS.items()):
    HOSTS[host] = (address, SOCKETS[host], data)

PRECEDENT = STORAGE / "case-budgets/sf008-att08.json"
PRECEDENT_TERMINAL = STORAGE / "results/SF008-ATT08-BUDGET-NOFAULT-01/terminal.json"
WORKER1_QUALIFICATION = STORAGE / "results/SF007-ATT05-BUDGET-NOFAULT-01/terminal.json"
WORKER1_BUDGET = STORAGE / "case-budgets/sf007-att05.json"
PRECEDENT_SHA = "7c657f3184e135c6f1228812886a48c34add406295af3974e30a30e03ecdc082"
TERMINAL_SHA = "dd560b1740f500a92c8ab56b6b02014b41aeb43b8bf247148cd95445a9b48a57"
RESERVES = {"master": 7_350_324_113, "worker1": 7_350_324_113,
            "worker2": 2_000_000_000, "worker3": 2_000_000_000}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def forecast(contract):
    precedent = json.loads(PRECEDENT.read_text())
    terminal = json.loads(PRECEDENT_TERMINAL.read_text())
    worker1 = json.loads(WORKER1_QUALIFICATION.read_text())
    if (sha(PRECEDENT) != PRECEDENT_SHA or sha(PRECEDENT_TERMINAL) != TERMINAL_SHA or
            terminal["status"] != "SF008_ATT08_BUDGET_NOFAULT_1800_PASS" or
            terminal["faults_injected"] != 0 or terminal["samples"] < 120 or
            precedent["data_reservation_bytes"]["master"] != RESERVES["master"] or
            worker1["status"] != "SF007_ATT05_BUDGET_NOFAULT_1800_PASS" or
            worker1["case_budget_sha256"] != sha(WORKER1_BUDGET) or
            worker1["samples"] < 120 or worker1["faults_injected"] != 0 or
            contract.get("status") != "PREREGISTERED_CASE_BUDGET_V2" or
            contract.get("run_id") != "SF-013-ATTEMPT-02" or
            contract.get("duration_seconds") != 1800 or
            contract.get("absolute_free_floor_bytes") != 5_000_000_000 or
            contract.get("root_reservation_bytes_per_host") != 64 * 1024**2 or
            contract.get("archive_target") != "worker1" or
            contract.get("data_reservation_bytes") != RESERVES or
            contract.get("precedent_contract_sha256") != PRECEDENT_SHA or
            contract.get("precedent_nofault_sha256") != TERMINAL_SHA):
        raise RuntimeError("SF-013 budget or precedent qualification changed")
    return {host: {"root_free_bytes": contract["root_reservation_bytes_per_host"],
                   **({"data_free_bytes": RESERVES[host]} if HOSTS[host][2] else {})}
            for host in HOSTS}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", type=Path, default=HERE / "case-budget.json")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise RuntimeError("Refusing to overwrite capacity evidence")
    contract = json.loads(args.contract.read_text())
    budget = forecast(contract)
    snapshot = {host: remote(host) for host in HOSTS}
    decision = decide(snapshot, budget)
    result = {"status": "READ_ONLY_FIVE_GB_SNAPSHOT", "at_utc":
              datetime.now(timezone.utc).isoformat(), "run_id": contract["run_id"],
              "snapshot": snapshot, "forecast": budget, "one_case": decision,
              "commitment_contract_sha256": sha(args.contract),
              "preflight_source_sha256": sha(__file__),
              "archive_target": contract["archive_target"], "faults_injected": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": decision["status"], "run_id": contract["run_id"],
                      "worker1_after_reservation_bytes":
                      snapshot["worker1"]["data_free_bytes"] - RESERVES["worker1"]}))
    if decision["status"] != "DYNAMIC_STORAGE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
