"""Qualified workflows unchanged; append-only request timing and neutral public User-Agent."""
import sys,types,json,threading
from pathlib import Path
from datetime import datetime,timezone
SOURCE=Path('/root/cross-layer-observability-control-rehearsal/scripts/workload_generator_rehearsal.py')
def main():
    source=SOURCE.read_text();old="'User-Agent': 'stage5-workload-rehearsal/1.0'"
    if source.count(old)!=1:raise RuntimeError('Qualified workload source interface changed')
    module=types.ModuleType('qualified_workload_with_public_metadata');module.__file__=str(SOURCE)
    anchor="    start = time.monotonic()" if 'workload' in __file__ else "    started=time.monotonic()"
    # Exact single request-function anchor; all other timing remains unchanged.
    prefix,tail=source.split('def execute_request(' if 'workload' in __file__ else 'def request(',1)
    if tail.count(anchor)<1:raise RuntimeError('Request journal interface changed')
    tail=tail.replace(anchor,"    record_intent(trace_id, operation)\n"+anchor,1)
    source=prefix+('def execute_request(' if 'workload' in __file__ else 'def request(')+tail
    exec(compile(source.replace(old,"'User-Agent': 'business-workload/1.0'"),str(SOURCE),'exec'),module.__dict__)
    output=Path(sys.argv[sys.argv.index('--output')+1]);original=module.execute_request;lock=threading.Lock()
    def record_intent(trace_id,operation):
        row={'trace_id':trace_id,'operation':operation,'at_utc':datetime.now(timezone.utc).isoformat(),'state':'HTTP_DISPATCH_ATTEMPT','not_a_proof_of_proxy_arrival':True}
        with lock:
            with (output/'request-dispatch-journal.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush()
    module.record_intent=record_intent
    def recorded(*args,**kwargs):
        began=datetime.now(timezone.utc).isoformat();row=original(*args,**kwargs)
        row['request_started_utc']=began
        with lock:
            with (output/'request-events-live.jsonl').open('a') as f:f.write(json.dumps(row)+'\n');f.flush()
        return row
    module.execute_request=recorded;module.main()
if __name__=='__main__':main()
