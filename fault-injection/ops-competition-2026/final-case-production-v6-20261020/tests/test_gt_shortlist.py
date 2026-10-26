import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('draft_anomalies',
                                              SCRIPTS / 'draft_anomalies.py')
draft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(draft)


class GTShortlistTests(unittest.TestCase):
    def test_at_most_five_without_dropping_other_candidates(self):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        events = [{'event_id': f'EVT-{index:04d}',
                   'start_time': start.isoformat(),
                   'end_time': (start + timedelta(seconds=90 + index)).isoformat(),
                   'modalities': ['Metrics'], 'candidate_ids': [f'ANOM-{index}'],
                   'evidence': [{'file': 'metrics.parquet'}]}
                  for index in range(1, 8)]
        result = draft.propose_scoring_events(events)
        self.assertEqual(len(result['scoring_event_ids']), 5)
        self.assertEqual(len(result['excluded_events']), 2)
        self.assertTrue(all(item['review_status'] ==
                            'PENDING_HUMAN_DISPOSITION'
                            for item in result['excluded_events']))
        self.assertFalse(result['formal_scoring_gt'])


if __name__ == '__main__':
    unittest.main()
