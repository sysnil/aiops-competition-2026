"""Hash every compact artifact in the independent diagnostics workspace."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import stat


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.root not in args.output.parents:
        raise ValueError("Need new manifest inside independent workspace")
    files = {}
    for path in sorted(args.root.rglob("*")):
        if "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError("Unexpected symlink in evidence tree")
        if not path.is_file():
            continue
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            raise ValueError("Nonregular evidence file")
        relative = str(path.relative_to(args.root))
        files[relative] = {"bytes": path.stat().st_size, "sha256": sha(path)}
    result = {"status": "DIAGNOSTICS_EVIDENCE_MANIFEST_FROZEN",
        "at_utc": datetime.now(timezone.utc).isoformat(),
        "root": str(args.root), "file_count": len(files),
        "total_compact_bytes": sum(row["bytes"] for row in files.values()),
        "raw_uid_canary_archive_manifest_sha256": sha(args.root /
            "archive/uid-canary-source-manifest.json"),
        "files": files, "faults_injected": 0,
        "stage5_v3_protocol_frozen": False,
        "formal_cases_created": 0}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"],
        "file_count": len(files), "total_bytes": result["total_compact_bytes"],
        "manifest_sha256": sha(args.output)}))


if __name__ == "__main__":
    main()
