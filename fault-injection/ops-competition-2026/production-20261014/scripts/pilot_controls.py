"""Original fixed-rate ordinary business streams; live, exact request timing evidence."""
import types,sys,json,threading
from pathlib import Path
from datetime import datetime,timezone
SOURCE=Path('/root/stage5-v3-autonomous-preparation/scripts/fixed_control_load_c3.py')
def main():
    source=SOURCE.read_text()
    if source.count("'User-Agent':'oc-business-workflow/1.0'")!=1:raise RuntimeError('Control source interface changed')
    module=types.ModuleType('qualified_controls_public_metadata');module.__file__=str(SOURCE)
    exec(compile(source.replace("'User-Agent':'oc-business-workflow/1.0'","'User-Agent':'business-control/1.0'"),str(SOURCE),'exec'),module.__dict__)
    output=Path(sys.argv[sys.argv.index('--output')+1]);original=module.request;lock=threading.Lock()
    def recorded(*args,**kwargs):
        began=datetime.now(timezone.utc).isoformat();row=original(*args,**kwargs);row['request_started_utc']=began
        with lock:
            with (output/'control-events-live.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush()
            if not row['success']:
                p=output/(row['operation']+'-failure.json')
                if not p.exists():p.write_text(json.dumps(row)+'\n')
        return row
    module.request=recorded;module.main()
if __name__=='__main__':main()
