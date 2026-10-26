import sys
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from contest_exchange_schema import iso_ns,log_record,trace_record,private_leaks

class ExchangeTests(unittest.TestCase):
    def test_nanoseconds_not_truncated(self):
        self.assertEqual(iso_ns('2026-10-13T00:00:00.123456789Z')-iso_ns('2026-10-13T00:00:00Z'),123456789)
    def test_timezone(self):
        self.assertEqual(iso_ns('2026-10-13T08:00:00+08:00'),iso_ns('2026-10-13T00:00:00Z'))
    def test_log_unknown_not_inferred_from_body(self):
        r=log_record({'@timestamp':'2026-10-13T00:00:00Z','body':'ERROR something'})
        self.assertEqual(r['severity'],'UNKNOWN');self.assertIsNone(r['pod'])
    def test_no_parent_or_ok_fabrication(self):
        r=trace_record({'traceID':'a'*32,'spanID':'b'*16,'startTime':123,'duration':1500,
            'operationName':'GET','process':{'serviceName':'frontend'},'references':[]},None)
        self.assertIsNone(r['parent_service']);self.assertIsNone(r['parent_span_id'])
        self.assertEqual(r['status'],'UNSET');self.assertEqual(r['duration_ms'],1.5)
    def test_private_guard(self):self.assertTrue(private_leaks({'body':'SF-PILOT-01'}))

if __name__=='__main__':unittest.main()
