import sys,unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from local_scheduler import validate_lease,onset
def utc(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.plan={'planned_utc':utc(1000)}
        self.clock={'measured_at_master_utc':utc(999),'worker_minus_master_seconds':.01,'uncertainty_seconds':.02}
        self.lease={'plan_sha256':'x','authorized':True,'issued_utc':utc(999),'expires_utc':utc(1003),'clock':self.clock,'safety_pass':True}
    def test_good_lease(self):validate_lease(self.plan,'x',self.lease,1000)
    def test_expired(self):
        with self.assertRaisesRegex(RuntimeError,'EXPIRED'):validate_lease(self.plan,'x',self.lease,1004)
    def test_revoked(self):
        with self.assertRaisesRegex(RuntimeError,'REVOKED'):validate_lease(self.plan,'x',self.lease,1000,True)
    def test_wrong_hash(self):
        with self.assertRaisesRegex(RuntimeError,'HASH'):validate_lease(self.plan,'bad',self.lease,1000)
    def test_unsafe(self):
        self.lease['safety_pass']=False
        with self.assertRaises(RuntimeError):validate_lease(self.plan,'x',self.lease,1000)
    def test_unknown_clock(self):
        self.clock['uncertainty_seconds']=.6
        with self.assertRaisesRegex(RuntimeError,'CLOCK_BOUND'):validate_lease(self.plan,'x',self.lease,1000)
    def test_old_failure_rejected(self):self.assertFalse(onset(self.plan,utc(1002.565669),self.clock)['pass'])
    def test_uncertainty_not_ignored(self):self.assertFalse(onset(self.plan,utc(1001.999),self.clock)['pass'])
    def test_small_deviation(self):self.assertTrue(onset(self.plan,utc(1000.12),self.clock)['pass'])
if __name__=='__main__':unittest.main()
