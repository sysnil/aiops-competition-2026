"""Cross-process capture-byte reservation. Hitting a budget FAILS the run; no row dropping."""
import fcntl,json,os
from pathlib import Path
def write_json(path,value):
    root=Path(os.environ['A04_CAPTURE_ROOT']).resolve();path=Path(path)
    if not path.resolve().is_relative_to(root):raise RuntimeError('CAPTURE_PATH_OUTSIDE_CASE')
    payload=(json.dumps(value,ensure_ascii=True,separators=(',',':'))+'\n').encode()
    ledger=Path(os.environ['A04_BUDGET_LEDGER']);ledger.parent.mkdir(parents=True,exist_ok=True)
    cap=int(os.environ['A04_JSON_CAP_BYTES'])
    with ledger.open('a+') as f:
        fcntl.flock(f,fcntl.LOCK_EX);f.seek(0);raw=f.read();used=int(raw or 0)
        if used+len(payload)>cap:raise RuntimeError('CAPTURE_JSON_CAP_EXCEEDED_NO_TRUNCATION')
        if path.exists():raise RuntimeError('IMMUTABLE_CAPTURE_ALREADY_EXISTS')
        # Reservations survive failed writes and therefore overestimate disk use.
        f.seek(0);f.truncate();f.write(str(used+len(payload)));f.flush();os.fsync(f.fileno())
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(payload)
