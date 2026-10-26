import sys,json,hashlib,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from registered_timers import verify_present
class TimerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.run='NEW-RUN';self.mapping={}
        for layer,host in [('SERVICE','k8s-worker3'),('POD','k8s-worker2')]:
            p=self.root/('scheduled-'+self.run+'-'+layer.lower()+'.json')
            p.write_text(json.dumps({'id':self.run+'-'+layer,'mode':'FAULT','worker':host,'unit':'ops-new-run-'+layer.lower()}))
            self.mapping[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        self.lock=self.root/'explicit-lock.json';self.lock.write_text(json.dumps(self.mapping))
    def tearDown(self):self.temp.cleanup()
    def test_current_run_file_names(self):self.assertTrue(verify_present({'ops-new-run-pod.timer'},self.lock,self.root,self.run,'k8s-worker2')['pass'])
    def test_other_host_not_accepted(self):self.assertFalse(verify_present({'ops-new-run-service.timer'},self.lock,self.root,self.run,'k8s-worker2')['pass'])
    def test_unknown_timer_not_accepted(self):self.assertFalse(verify_present({'ops-unrelated.timer'},self.lock,self.root,self.run,'k8s-worker2')['pass'])
    def test_hash_mismatch_fails(self):
        Path(next(iter(self.mapping))).write_text('{}')
        with self.assertRaisesRegex(ValueError,'HASH'):verify_present(set(),self.lock,self.root,self.run,'k8s-worker2')
    def test_wrong_run_fails(self):
        with self.assertRaises(ValueError):verify_present(set(),self.lock,self.root,'OLD','k8s-worker2')
    def test_missing_lock_fails(self):
        with self.assertRaises(FileNotFoundError):verify_present(set(),self.root/'missing.json',self.root,self.run,'k8s-worker2')
    def test_all_cancelled_ok(self):self.assertTrue(verify_present(set(),self.lock,self.root,self.run,'k8s-worker2')['pass'])
    def test_duplicate_scope_fails(self):
        self.lock.write_text(json.dumps({next(iter(self.mapping)):next(iter(self.mapping.values()))}))
        with self.assertRaises(ValueError):verify_present(set(),self.lock,self.root,self.run,'k8s-worker2')
if __name__=='__main__':unittest.main()
