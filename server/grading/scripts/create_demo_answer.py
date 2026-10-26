from __future__ import annotations

import hashlib
import json
from pathlib import Path


def digest(record: dict) -> str:
    raw = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "demo/evidence.json").read_text(encoding="utf-8"))
    records = {item["modality"]: item["record"] for item in fixture["records"]}
    evidence = []
    for modality in ("logs", "traces", "metrics"):
        record = records[modality]
        evidence.append(
            {
                "modality": modality,
                "record_sha256": digest(record),
                "record": record,
            }
        )
    answer = {
        "case_id": "DEMO-001",
        "schema_version": "2.0",
        "anomalies": [
            {
                "entity_type": "Service",
                "entity_id": "demo-payment",
                "suspected_fault_type": "ServicePause",
                "start_time": "2030-01-01T00:20:00+00:00",
                "end_time": "2030-01-01T00:22:00+00:00",
                "evidence": evidence,
            }
        ],
    }
    output = root / "demo/demo-answer.json"
    output.write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()

