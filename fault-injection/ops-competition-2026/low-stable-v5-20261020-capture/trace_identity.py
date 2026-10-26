"""Pure identity and content-integrity checks shared by trace capture/evaluator."""

import hashlib
import json


def span_map(root):
    found = {}
    for path in sorted((root / "trace-storage-pages").glob("page-*.json")):
        for hit in json.loads(path.read_text())["hits"]["hits"]:
            row = hit["_source"]
            key = (row["traceID"].lower(), row["spanID"].lower())
            content = hashlib.sha256(json.dumps(row, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            if key in found:
                raise ValueError("Duplicate source trace/span ID")
            found[key] = (content, row)
    if not found:
        raise ValueError("Empty trace source pages")
    return found


def subset(earlier, later):
    if not earlier.keys() <= later.keys():
        raise ValueError("Earlier trace/span identity missing from later read")
    if any(earlier[key][0] != later[key][0] for key in earlier):
        raise ValueError("Same trace/span ID changed content")
