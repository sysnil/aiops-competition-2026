import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from archive_batch import digest, inventory, verify
from restore_archived_file import validate_path


class ArchiveTests(unittest.TestCase):
    def test_restore_path_rejects_traversal(self):
        for value in ("../private", "/root/file", "a/../../b"):
            with self.assertRaises(ValueError):
                validate_path(value)
        self.assertEqual(str(validate_path("case/metrics.json")),
                         "case/metrics.json")
    def test_inventory_rejects_unregistered_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                inventory("mechanism-runs", root, root / "manifest.json")

    def test_verify_detects_missing_or_changed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            manifest = root / "manifest.json"
            file = archive / "trace.jsonl"
            file.write_text('{"traceID":"x"}\n')
            row = file.stat()
            value = {"batch": "synthetic", "file_count": 1,
                     "total_bytes": row.st_size, "directories": {},
                     "files": {"trace.jsonl": {"bytes": row.st_size,
                         "sha256": digest(file), "mode": row.st_mode & 0o7777,
                         "uid": row.st_uid, "gid": row.st_gid,
                         "mtime_ns": row.st_mtime_ns}}}
            manifest.write_text(json.dumps(value))
            with patch("archive_batch.socket.gethostname", return_value="k8s-worker2"):
                self.assertEqual(verify(manifest, archive)["status"],
                                 "ARCHIVE_VERIFIED")
            file.write_text("modified")
            with patch("archive_batch.socket.gethostname", return_value="k8s-worker2"):
                with self.assertRaises(RuntimeError):
                    verify(manifest, archive)


if __name__ == "__main__":
    unittest.main()
