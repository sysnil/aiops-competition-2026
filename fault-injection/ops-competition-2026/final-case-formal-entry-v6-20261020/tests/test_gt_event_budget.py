import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from check_gt_event_budget import assess


def event(event_id, end='2026-10-19T00:03:00+00:00'):
    return {'event_id': event_id, 'start_time': '2026-10-19T00:00:00+00:00',
            'end_time': end, 'candidate_ids': ['ANOM-' + event_id],
            'evidence': [{'modality': 'Traces'}]}


class EventBudgetTests(unittest.TestCase):
    def test_five_evidenced_events_pass_without_rewriting_draft(self):
        draft = {'candidate_count': 23,
                 'merged_events': [event(f'EVT-{n:04d}') for n in range(1, 7)]}
        shortlist = {'scoring_event_ids': [f'EVT-{n:04d}' for n in range(1, 6)],
                     'excluded_events': [{'event_id': 'EVT-0006',
                                          'reason': 'Public evidence judged insufficient',
                                          'review_status': 'HUMAN_REJECTED'}],
                     'review_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
        self.assertEqual(assess(draft, shortlist)['status'],
                         'GT_SHORTLIST_READY_FOR_HUMAN_REVIEW')
        self.assertEqual(len(draft['merged_events']), 6)

    def test_overflow_short_and_unknown_events_block_review(self):
        draft = {'candidate_count': 6,
                 'merged_events': [event(f'EVT-{n:04d}') for n in range(1, 7)]}
        shortlist = {'scoring_event_ids': [f'EVT-{n:04d}' for n in range(1, 7)],
                     'excluded_events': [],
                     'review_status': 'DRAFT_PENDING_HUMAN_REVIEW'}
        self.assertIn('EVENT_COUNT_OUT_OF_RANGE', assess(draft, shortlist)['issues'])
        draft['merged_events'][0]['end_time'] = '2026-10-19T00:00:30+00:00'
        shortlist['scoring_event_ids'] = ['EVT-0001', 'EVT-9999']
        self.assertEqual(assess(draft, shortlist)['status'],
                         'GT_SHORTLIST_REVIEW_BLOCKED')


if __name__ == '__main__':
    unittest.main()
