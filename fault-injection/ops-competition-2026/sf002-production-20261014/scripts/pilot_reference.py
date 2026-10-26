"""Compact real no-fault reference statistics; no injected cases or GT are read."""
import statistics
from collections import defaultdict
from pilot_common import *
from pilot_exchange_schema import canonical,span_attributes
def main():
    values=defaultdict(list);traces=defaultdict(list);provenance={}
    root=DATA/'collector-telemetry-repair-20261013/runs-v3'
    for role in ('VAL1','VAL2'):
        run=root/role;assessment=read(run/'assessment.json');schedule=read(run/'schedule.json')
        if assessment['status']!='NO_FAULT_VALIDATION_PASS' or schedule['config_sha256']!=EXPECTED_CONFIG:raise RuntimeError('Incomparable or invalid reference')
        provenance[role]={'assessment_sha256':sha(run/'assessment.json'),'schedule_sha256':sha(run/'schedule.json'),'seed':schedule['seed']}
        for file in (run/'capture').glob('*/captures/during/metrics/memory.json'):
            for series in read(file)['data']['result']:
                m=series['metric'];key=canonical([m.get('namespace'),m.get('pod'),m.get('id')])
                values[key].extend(float(v) for _,v in series['values'])
        for file in (run/'capture').glob('*/trace-final/during/trace-storage-pages/page-*.json'):
            for h in read(file)['hits']['hits']:
                s=h['_source'];key=canonical([s['process']['serviceName'],s['operationName'],span_attributes(s).get('span.kind','UNKNOWN')])
                traces[key].append(s['duration']/1000)
    memory={}
    for key,v in values.items():
        center=statistics.median(v);memory[key]={'samples':len(v),'median_bytes':center,'mad_bytes':statistics.median(abs(x-center) for x in v)}
    latency={k:{'samples':len(v),'p95_ms':sorted(v)[int((len(v)-1)*.95)]} for k,v in traces.items() if len(v)>=10}
    result={'status':'OBSERVED_NO_FAULT_REFERENCE','at_utc':now(),'memory':memory,'traces':latency,'provenance':provenance,
        'comparability':{'collector_config_sha256':EXPECTED_CONFIG,'rate_sessions_per_second':1.4,'controls_rates':[1,2],
            'limits':['different seeds and time of day','different process uptime/cache state','memory reference only joined on the exact public Pod/cgroup identity','not a fully independent calibrated null distribution']},
        'injection_plan_or_gt_read':False}
    save(PROD/'results/no-fault-reference.json',result);print(json.dumps({'status':result['status'],'memory_series':len(memory),'trace_operation_groups':len(latency)}))
if __name__=='__main__':main()
