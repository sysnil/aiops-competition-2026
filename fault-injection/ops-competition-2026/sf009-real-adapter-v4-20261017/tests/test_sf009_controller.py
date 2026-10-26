import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf009_real_controller',
                                              SCRIPTS / 'sf009_controller.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HTTPStageResult(unittest.TestCase):
    def test_sixty_real_home_requests_in_window(self):
        with tempfile.TemporaryDirectory() as directory:
            start = datetime.now(timezone.utc)
            plan = {'start_utc': start.isoformat()}
            path = Path(directory) / 'events.jsonl'
            rows = [{'at_utc': (start + timedelta(seconds=600+i)).isoformat(),
                     'delay_header_applied': True} for i in range(60)]
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            result = MODULE.check_http_headers(path, plan,
                (start + timedelta(seconds=661)).timestamp())
            self.assertEqual(result['delayed_home_requests'], 60)

    def test_out_of_window_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            start = datetime.now(timezone.utc)
            path = Path(directory) / 'events.jsonl'
            path.write_text(json.dumps({'at_utc': (start + timedelta(seconds=599)).isoformat(),
                'delay_header_applied': True}) + '\n')
            with self.assertRaisesRegex(RuntimeError, 'outside registered window'):
                MODULE.check_http_headers(path, {'start_utc': start.isoformat()},
                                          (start + timedelta(seconds=661)).timestamp())


if __name__ == '__main__':
    unittest.main()
