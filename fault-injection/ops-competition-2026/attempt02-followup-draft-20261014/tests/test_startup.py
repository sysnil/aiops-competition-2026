import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from safety_startup_gate import classify
class StartupTests(unittest.TestCase):
    def test_at_t0_not_false_failure_or_authorization(self):
        self.assertEqual(classify(100,100),{'state':'WAITING_FOR_FIRST_SAMPLE','may_authorize_fault':False})
    def test_sample_in_progress(self):self.assertFalse(classify(101,100)['may_authorize_fault'])
    def test_missing_at_deadline_fails(self):
        with self.assertRaises(RuntimeError):classify(115,100)
    def test_real_first_sample_pass(self):self.assertTrue(classify(103,100,102)['may_authorize_fault'])
    def test_old_sample_not_bootstrap(self):
        with self.assertRaises(RuntimeError):classify(100,100,99)
    def test_stale_bound_not_relaxed(self):
        with self.assertRaises(RuntimeError):classify(150.01,100,120)
    def test_future_not_valid(self):
        with self.assertRaises(RuntimeError):classify(110,100,111)
    def test_pre_window_not_authorized(self):self.assertFalse(classify(99,100)['may_authorize_fault'])
if __name__=='__main__':unittest.main()
