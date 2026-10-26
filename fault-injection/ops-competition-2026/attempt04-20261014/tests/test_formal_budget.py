import os,sys,tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from bounded_storage import write_json
class Budget(unittest.TestCase):
 def test_all_sources_compile(self):
  for p in Path(__file__).resolve().parents[1].rglob('*.py'):compile(p.read_text(),str(p),'exec')
 def test_reserve_before_write_no_truncation(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);env={'A04_CAPTURE_ROOT':d,'A04_BUDGET_LEDGER':str(p/'ledger'),'A04_JSON_CAP_BYTES':'10'}
   with patch.dict(os.environ,env):
    write_json(p/'a.json',{})
    with self.assertRaisesRegex(RuntimeError,'CAP_EXCEEDED'):write_json(p/'b.json',{'long':'x'*20})
    self.assertFalse((p/'b.json').exists());self.assertEqual((p/'a.json').read_text(),'{}\n')
 def test_existing_and_escape_refused(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   with patch.dict(os.environ,{'A04_CAPTURE_ROOT':d,'A04_BUDGET_LEDGER':str(p/'ledger'),'A04_JSON_CAP_BYTES':'100'}):
    write_json(p/'a.json',{})
    with self.assertRaisesRegex(RuntimeError,'ALREADY_EXISTS'):write_json(p/'a.json',{})
    with self.assertRaisesRegex(RuntimeError,'OUTSIDE_CASE'):write_json(p.parent/'escape.json',{})
if __name__=='__main__':unittest.main()
