import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from boundary_empty import verified_empty
class AuxiliaryEmptyTests(unittest.TestCase):
    def setUp(self):self.r={'timed_out':False,'_shards':{'total':1,'successful':1,'failed':0},'hits':{'total':{'value':0,'relation':'eq'},'hits':[]}}
    def test_verified_zero(self):self.assertTrue(verified_empty(self.r))
    def test_nonzero_still_requires_full_reader(self):self.r['hits']['total']['value']=1;self.assertFalse(verified_empty(self.r))
    def test_zero_shards_not_empty(self):
        self.r['_shards']['total']=0
        with self.assertRaises(ValueError):verified_empty(self.r)
    def test_timeout_not_empty(self):
        self.r['timed_out']=True
        with self.assertRaises(ValueError):verified_empty(self.r)
    def test_failed_shard_not_empty(self):
        self.r['_shards']['failed']=1
        with self.assertRaises(ValueError):verified_empty(self.r)
    def test_inexact_not_empty(self):
        self.r['hits']['total']['relation']='gte'
        with self.assertRaises(ValueError):verified_empty(self.r)
    def test_unknown_not_empty(self):
        del self.r['timed_out']
        with self.assertRaises(ValueError):verified_empty(self.r)
if __name__=='__main__':unittest.main()
