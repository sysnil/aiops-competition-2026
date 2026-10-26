"""Recompute a private exported ranking without accessing the live database."""
import argparse
import hashlib
import json
from pathlib import Path

from semifinal_results import instant, rank_rows

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
args=parser.parse_args()
root=args.directory
old=json.loads((root/'results.json').read_text())
plan_raw=(root/'ranking-plan.json').read_bytes()
rows_raw=(root/'input-rows.json').read_bytes()
assert hashlib.sha256(plan_raw).hexdigest()==old['plan_sha256'],'plan hash mismatch'
assert hashlib.sha256(rows_raw).hexdigest()==old['input_rows_sha256'],'input hash mismatch'
new=rank_rows(json.loads(rows_raw),json.loads(plan_raw),now=instant(old['generated_at_utc']))
for field in ('ranking_version','status','leaderboard','submission_audit','session_summaries'):
    assert new[field]==old[field],f'recomputed {field} differs'
print(json.dumps({'status':'RANKING_RECOMPUTATION_VERIFIED','teams':len(new['leaderboard']),'ranking_version':new['ranking_version']}))
