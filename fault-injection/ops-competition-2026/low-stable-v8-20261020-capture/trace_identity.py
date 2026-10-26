"""Pure identity and content-integrity checks shared by trace capture/evaluator."""

import hashlib
import json

from trace_storage_reader import MAX_IDENTICAL_DUPLICATE_FRACTION


def span_map(root):
    found = {}
    document_ids = {}
    duplicates = []
    raw_hits = 0
    for path in sorted((root / "trace-storage-pages").glob("page-*.json")):
        for hit in json.loads(path.read_text())["hits"]["hits"]:
            row = hit["_source"]
            key = (row["traceID"].lower(), row["spanID"].lower())
            content = hashlib.sha256(json.dumps(row, sort_keys=True,
                separators=(",", ":")).encode()).hexdigest()
            raw_hits += 1
            if key in found:
                if found[key][0] != content:
                    raise ValueError('Conflicting source trace/span ID')
                duplicates.append({'trace_id': key[0], 'span_id': key[1],
                    'first_document_id': document_ids[key],
                    'duplicate_document_id': hit['_id'],
                    'source_sha256': content})
                continue
            found[key] = (content, row)
            document_ids[key] = hit['_id']
    if not found:
        raise ValueError("Empty trace source pages")
    metadata = json.loads((root / 'trace-storage-source.json').read_text())
    audit = json.loads((root / 'trace-storage-duplicate-audit.json').read_text())
    if (metadata['total_storage_hits'] != raw_hits or
            metadata['unique_spans'] != len(found) or
            metadata['identical_duplicate_hits'] != len(duplicates) or
            audit['duplicate_documents'] != duplicates or
            len(duplicates) > raw_hits * MAX_IDENTICAL_DUPLICATE_FRACTION):
        raise ValueError('Trace duplicate audit or raw page count mismatch')
    return found


def subset(earlier, later):
    if not earlier.keys() <= later.keys():
        raise ValueError("Earlier trace/span identity missing from later read")
    if any(earlier[key][0] != later[key][0] for key in earlier):
        raise ValueError("Same trace/span ID changed content")
