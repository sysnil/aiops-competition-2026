import sys,unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from pilot_common import boundary_match,OFFSETS
from pilot_exchange_schema import iso_ns,log_record,trace_record,private_leaks
class ProductionRules(unittest.TestCase):
    def test_fault_interval_stays_in_one_capture(self):
        windows=list(zip(OFFSETS,OFFSETS[1:]));self.assertEqual(windows[0][0],0);self.assertEqual(windows[-1][1],3600)
        self.assertEqual(sum(b-a for a,b in windows),3600)
        self.assertTrue(any(a<=720 and b>=1020 for a,b in windows))
    def test_only_real_same_identity_at_edge_can_resolve(self):
        r={'trace_id':'a'*32,'request_started_utc':datetime.fromtimestamp(1000.001,timezone.utc).isoformat()}
        s={'traceID':'a'*32,'startTime':999997000,'process':{'serviceName':'frontend-proxy'},'tag':{'span@kind':'server'}}
        self.assertTrue(boundary_match(r,s,1000,4600))
        s['traceID']='b'*32;self.assertFalse(boundary_match(r,s,1000,4600))
        s['traceID']='a'*32;r['request_started_utc']=datetime.fromtimestamp(1003,timezone.utc).isoformat();self.assertFalse(boundary_match(r,s,1000,4600))
    def test_missing_is_null_not_a_synthetic_label(self):
        row=log_record({'@timestamp':'2026-10-14T00:00:00.123456789Z'})
        self.assertIsNone(row['body']);self.assertIsNone(row['severity']);self.assertIsNone(row['attributes'])
        self.assertEqual(row['timestamp']%10**9,123456789)
    def test_original_short_trace_id_preserved(self):
        row=trace_record({'traceID':'a'*16,'spanID':'b'*16,'startTime':12,'duration':120,'process':{'serviceName':'x'},'operationName':'op'},None)
        self.assertEqual(row['trace_id'],'a'*16);self.assertIsNone(row['status'])
    def test_private_provenance_is_rejected(self):
        self.assertTrue(private_leaks({'body':'/root/ops-competition-2026/plans/SF-PILOT-01.json'}))
        self.assertFalse(private_leaks({'body':'normal observable application timeout'}))
if __name__=='__main__':unittest.main()
