import json
from pathlib import Path
import tempfile
import unittest

import archive_clean


def write(folder, name, value):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class ArchiveTests(unittest.TestCase):
    def fixture(self, folder):
        write(folder, 'schedule.json', {'run_id': 'demo'})
        write(folder, 'run-result.json', {'status': 'RAW_CLEAN_CAPTURE_COMPLETE_AWAITING_QUALITY_AND_ARCHIVE'})
        write(folder, 'load_pair/state-machine/sessions.jsonl', {'steps': []})
        write(folder, 'load_pair/controls/image_asset.jsonl', {'success': True})
        write(folder, 'load_pair/controls/cart_view.jsonl', {'success': True})
        write(folder, 'capture-pipeline/pipeline-result.json', {'status': 'CAPTURE_PIPELINE_PASS'})
        write(folder, 'capture-pipeline/captures/during/metrics/cpu.json',
              {'status': 'success', 'data': {'result': []}})
        write(folder, 'capture-pipeline/captures/during/logs/page-0000.json',
              {'hits': {'hits': []}})
        write(folder, 'capture-pipeline/captures/during/trace-storage-pages/page-0000.json',
              {'hits': {'hits': []}})

    def test_full_inventory_restore_and_sha_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / 'master', root / 'worker'
            self.fixture(a); self.fixture(b)
            self.assertEqual(archive_clean.inventory(a), archive_clean.inventory(b))
            self.assertEqual(archive_clean.restore(b)['run_id'], 'demo')
            write(b, 'capture-pipeline/captures/during/metrics/cpu.json',
                  {'status': 'error', 'data': {'result': []}})
            self.assertNotEqual(archive_clean.inventory(a), archive_clean.inventory(b))
            with self.assertRaises(RuntimeError): archive_clean.restore(b)

    def test_final_health_stale_and_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'health.json'
            write(Path(tmp), 'health.json', {'status': 'GLOBAL_SAFETY_PASS',
                 'captured_at_utc': '2026-01-01T00:00:00+00:00'})
            with self.assertRaises(RuntimeError):
                archive_clean.fresh(path, 'GLOBAL_SAFETY_PASS')
            write(Path(tmp), 'failed.json', {'status': 'GLOBAL_SAFETY_FAIL',
                 'captured_at_utc': '2026-01-01T00:00:00+00:00'})
            with self.assertRaises(RuntimeError):
                archive_clean.fresh(Path(tmp) / 'failed.json', 'GLOBAL_SAFETY_PASS')


if __name__ == '__main__': unittest.main()
