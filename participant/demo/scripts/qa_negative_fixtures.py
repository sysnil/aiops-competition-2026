#!/usr/bin/env python3
"""Create schema-valid negative QA submissions from one real answer."""

from __future__ import annotations

import argparse
import copy
import json
from datetime import timedelta
from pathlib import Path

from qa_solver import iso_utc, parse_utc


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answer", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.answer.read_text(encoding="utf-8"))
    variants = {}

    item = copy.deepcopy(original)
    item["answers"][0]["value"] = float(item["answers"][0]["value"]) + 1000.0
    variants["wrong-answer"] = item

    item = copy.deepcopy(original)
    item["chart"]["series"][0]["name"] += "-wrong"
    variants["wrong-entity"] = item

    item = copy.deepcopy(original)
    point = item["chart"]["series"][0]["points"][0]
    point["timestamp"] = iso_utc(parse_utc(point["timestamp"]) + timedelta(seconds=1))
    variants["wrong-time"] = item

    item = copy.deepcopy(original)
    item["answers"][0]["unit"] = "wrong-unit"
    variants["wrong-unit"] = item

    item = copy.deepcopy(original)
    point = item["chart"]["series"][0]["points"][0]
    point["value"] = float(point["value"] or 0) + 1.0
    variants["tampered-chart"] = item

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in variants.items():
        (args.output_dir / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({"fixtures": sorted(variants)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
