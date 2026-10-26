import importlib.util
import json
import tempfile
import time
from pathlib import Path
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf008_marker_preflight',
                                              SCRIPTS / 'marker_preflight.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MatureChunkBarrier(unittest.TestCase):
    def test_missing_quality_fails_at_deadline(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, 'quality missing'):
                MODULE.wait_for_quality([Path(directory) / 'quality.json'], time.time() - 1)

    def test_failed_quality_cannot_authorize(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quality.json'
            path.write_text(json.dumps({'status': 'CHUNK_QUALITY_FAILED'}))
            with self.assertRaisesRegex(RuntimeError, 'quality missing'):
                MODULE.wait_for_quality([path], time.time() + 10)

    def test_all_required_passed_quality_hashes_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f'quality-{i}.json' for i in range(2)]
            for path in paths:
                path.write_text(json.dumps({'status': 'CHUNK_QUALITY_PASS'}))
            hashes = MODULE.wait_for_quality(paths, time.time() + 10)
            self.assertEqual(set(hashes), {str(path) for path in paths})


if __name__ == '__main__':
    unittest.main()
