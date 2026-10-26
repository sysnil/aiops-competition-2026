"""Small synthetic archive verifies every byte before any source pruning."""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from archive_rehearsal_run import (archive_run, paths, prune_verified_source,
                                   restore, verify_archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run_id = "S5V3AP-CLEAN-SYNTHETIC"
        self.source, self.archive, self.manifest = paths(self.root, "runs", self.run_id)
        (self.source / "capture-pipeline/captures/before").mkdir(parents=True)
        (self.source / "result.json").write_bytes(b'{"status":"synthetic complete"}\n')
        (self.source / "capture-pipeline/captures/before/page.json").write_bytes(
            b'{"traceID":"synthetic","spanID":"one"}\n')

    def tearDown(self):
        self.temp.cleanup()

    def test_archive_verify_prune_and_restore_are_lossless(self):
        original = (self.source / "capture-pipeline/captures/before/page.json").read_bytes()
        archive_run(self.source, self.archive, self.manifest, "runs", self.run_id)
        self.assertEqual(len(verify_archive(self.archive, self.manifest)["files"]), 2)
        receipt = prune_verified_source(self.source, self.archive, self.manifest)
        self.assertTrue(receipt.is_file())
        self.assertFalse(self.source.exists())
        target = self.root / "restored"
        restore(self.archive, self.manifest, target)
        self.assertEqual((target / self.run_id /
                          "capture-pipeline/captures/before/page.json").read_bytes(),
                         original)

    def test_corrupt_archive_never_prunes_source(self):
        archive_run(self.source, self.archive, self.manifest, "runs", self.run_id)
        with self.archive.open("ab") as stream:
            stream.write(b"corruption")
        with self.assertRaises(ValueError):
            prune_verified_source(self.source, self.archive, self.manifest)
        self.assertTrue(self.source.is_dir())


if __name__ == "__main__":
    unittest.main()
