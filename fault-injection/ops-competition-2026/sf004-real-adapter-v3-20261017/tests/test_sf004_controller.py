import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location('sf004_real_controller',
                                              SCRIPTS / 'sf004_controller.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HTTPStageResult(unittest.TestCase):
    def test_state_machine_and_fixed_home_are_counted_together(self):
        with tempfile.TemporaryDirectory() as directory:
            start = datetime.now(timezone.utc)
            paths = [Path(directory) / 'state.jsonl', Path(directory) / 'fixed.jsonl']
            for path, count in zip(paths, (35, 45)):
                rows = [{'at_utc': (start + timedelta(seconds=900+i)).isoformat(),
                         'delay_header_applied': True, 'requested_delay_ms': 250}
                        for i in range(count)]
                path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            result = MODULE.marked_home_events(paths,
                (start + timedelta(seconds=900)).timestamp(),
                (start + timedelta(seconds=960)).timestamp())
            self.assertEqual(result['delayed_requests'], 80)
            self.assertEqual(len(result['source_paths']), 2)

    def test_sixty_real_home_requests_in_window(self):
        with tempfile.TemporaryDirectory() as directory:
            start = datetime.now(timezone.utc)
            path = Path(directory) / 'events.jsonl'
            rows = [{'at_utc': (start + timedelta(seconds=900+i)).isoformat(),
                     'delay_header_applied': True, 'requested_delay_ms': 250}
                    for i in range(60)]
            path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n')
            result = MODULE.marked_home_events(path,
                (start + timedelta(seconds=900)).timestamp(),
                (start + timedelta(seconds=960)).timestamp())
            self.assertEqual(result['delayed_requests'], 60)

    def test_out_of_window_header_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            start = datetime.now(timezone.utc)
            path = Path(directory) / 'events.jsonl'
            path.write_text(json.dumps({'at_utc': (start + timedelta(seconds=899)).isoformat(),
                'delay_header_applied': True, 'requested_delay_ms': 250}) + '\n')
            with self.assertRaisesRegex(RuntimeError, 'escaped frozen window'):
                MODULE.marked_home_events(path,
                    (start + timedelta(seconds=900)).timestamp(),
                    (start + timedelta(seconds=960)).timestamp())


if __name__ == '__main__':
    unittest.main()
