import unittest

from case_utils import expected_duration_seconds


class CompetitionCaseDuration(unittest.TestCase):
    def test_practice_and_existing_sample_remain_one_hour(self):
        for case_id in ('SF-001', 'SF-002', 'IMPORT-SAMPLE-001'):
            self.assertEqual(expected_duration_seconds(case_id), 3600)

    def test_semifinal_cases_are_thirty_minutes(self):
        for case_id in ('SF-003', 'SF-009', 'SF-010', 'SF-012'):
            self.assertEqual(expected_duration_seconds(case_id), 1800)


if __name__ == '__main__':
    unittest.main()
