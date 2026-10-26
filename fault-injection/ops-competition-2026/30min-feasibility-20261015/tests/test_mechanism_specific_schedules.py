import importlib.util
from pathlib import Path
import unittest


PATH = Path(__file__).resolve().parents[1] / 'scripts/mechanism_specific_schedules.py'
SPEC = importlib.util.spec_from_file_location('mechanism_specific_schedules', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MechanismSpecificTiming(unittest.TestCase):
    def test_short_service_and_pod_preserve_distinct_durations(self):
        result = MODULE.schedule(['ServicePause', 'PodMemoryStress'],
                                 {'ServicePause': 30, 'PodMemoryStress': 60})
        self.assertEqual([item['duration_seconds'] for item in result['events']], [30, 60])
        self.assertEqual(result['complete_by_seconds'], 1245)
        self.assertFalse(result['production_authorized'])

    def test_four_event_case_only_narrow_mathematical_fit(self):
        names = ['NetworkDelay', 'HTTP RespDelay', 'PodMemoryStress', 'ServicePause']
        result = MODULE.schedule(names, {'PodMemoryStress': 60, 'ServicePause': 30})
        self.assertEqual([item['start_seconds'] for item in result['events']], [360, 750, 1140, 1530])
        self.assertEqual(result['complete_by_seconds'], 1785)
        self.assertTrue(result['requires_deferred_audit'])
        self.assertEqual(result['unknown_duration_mechanisms'], names[:2])

    def test_five_and_six_events_exceed_thirty_minutes(self):
        known = {'ServicePause': 30, 'PodMemoryStress': 60}
        for names in (
            ['PodCPUStress', 'HTTP ReqAbort', 'NetworkBandwidth', 'HTTP RespAbort', 'ServicePause'],
            ['NetworkLoss', 'HTTP RespReplaceCode', 'PodCPUStress', 'HTTP RespAbort',
             'NetworkBandwidth', 'PodMemoryStress'],
        ):
            result = MODULE.schedule(names, known)
            self.assertFalse(result['fits_mathematically'])
            self.assertTrue(result['no_overlap'])


if __name__ == '__main__':
    unittest.main()
