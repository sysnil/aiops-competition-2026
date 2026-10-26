import sys,unittest,tempfile,json,multiprocessing,time
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from handshake import Gate,digest,atomic
def utc(x):return datetime.fromtimestamp(x,timezone.utc).isoformat()
def sample(t=102,seq=0):
    h={'run_id':'r','run_token':'t','plan_sha256':'p','producer_sha256':'s','pid':5,'sequence':seq,'check_started_utc':utc(t-1),'completed_utc':utc(t),'status':'LIVE_ENVIRONMENT_PASS','checks':{'real_test_fixture':True}}
    h['checks_sha256']=digest(h['checks']);return h
def delayed_write(path,delay):time.sleep(delay);atomic(path,sample(),True)
class HandshakeTests(unittest.TestCase):
    def setUp(self):self.g=Gate('r','t','p','s',100,130)
    def test_normal(self):self.assertTrue(self.g.accept(sample(),103,5));self.assertEqual(self.g.state,'READY')
    def test_delayed_first(self):self.assertFalse(self.g.accept(None,105,5));self.assertTrue(self.g.accept(sample(113),114,5))
    def test_missing(self):
        with self.assertRaises(RuntimeError):self.g.accept(None,115,5)
    def test_unsafe(self):
        h=sample();h['status']='FAIL'
        with self.assertRaises(RuntimeError):self.g.accept(h,103,5)
    def test_old_run(self):
        h=sample();h['run_id']='old'
        with self.assertRaises(RuntimeError):self.g.accept(h,103,5)
    def test_stale_initial(self):
        with self.assertRaises(RuntimeError):self.g.accept(sample(60),103,5)
    def test_future(self):
        with self.assertRaises(RuntimeError):self.g.accept(sample(104),103,5)
    def test_malformed_timestamp(self):
        h=sample();h['completed_utc']='bad'
        with self.assertRaises(RuntimeError):self.g.accept(h,103,5)
    def test_exit(self):
        with self.assertRaises(RuntimeError):self.g.accept(sample(),103,5,False)
    def test_running_stale(self):
        self.g.accept(sample(),103,5)
        with self.assertRaises(RuntimeError):self.g.accept(sample(),135,5)
    def test_t0_unready(self):
        g=Gate('r','t','p','s',100,110)
        with self.assertRaises(RuntimeError):g.accept(None,110,5)
    def test_duplicate_identical_read_allowed(self):self.g.accept(sample(),103,5);self.assertTrue(self.g.accept(sample(),104,5))
    def test_duplicate_rewrite_rejected(self):
        self.g.accept(sample(),103,5)
        with self.assertRaises(RuntimeError):self.g.accept(sample(103),104,5)
    def test_sequence_identity(self):
        self.g.accept(sample(),103,5);self.g.accept(sample(117,1),118,5)
        with self.assertRaises(RuntimeError):self.g.accept(sample(118,0),119,5)
    def test_wrong_pid(self):
        with self.assertRaises(RuntimeError):self.g.accept(sample(),103,6)
    def test_partial_schema(self):
        with self.assertRaises(RuntimeError):self.g.accept({'run_id':'r'},103,5)
    def test_atomic_subprocess_publication(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'heartbeat.json';child=multiprocessing.Process(target=delayed_write,args=(p,.1));child.start()
            while child.is_alive():
                if p.exists():self.assertTrue(self.g.accept(json.loads(p.read_text()),103,5))
                time.sleep(.005)
            child.join();self.assertEqual(child.exitcode,0);self.assertTrue(self.g.accept(json.loads(p.read_text()),103,5))
    def test_immutable_duplicate_publish(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'sample.json';atomic(p,sample(),True)
            with self.assertRaises(FileExistsError):atomic(p,sample(103),True)
if __name__=='__main__':unittest.main()
