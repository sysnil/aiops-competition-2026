"""Post-run resource accounting from preserved samples; never turns missing into zero."""
from collections import defaultdict
from common import *
def main():
    result={'at_utc':now(),'runs':{},'scope':'observed samples, not unobserved instantaneous peaks'}
    for role in ('BASE','VAL1','VAL2'):
        run=DATA/'runs-v3'/role
        if not (run/'assessment.json').exists():continue
        assessment=read(run/'assessment.json');rows=[json.loads(l) for l in (run/'monitor.jsonl').read_text().splitlines()]
        cpu=[float(x['value'][1]) for r in rows for x in r['cpu']['data']['result']]
        heap=[x['max'] for x in assessment['metric_statistics'] if x['labels'].get('__name__')=='otelcol_process_runtime_heap_alloc_bytes']
        queues=[{'exporter':x['labels'].get('exporter'),'signal':x['labels'].get('data_type'),'max':x['max'],'samples':x['samples']}
            for x in assessment['metric_statistics'] if x['labels'].get('__name__')=='otelcol_exporter_queue_size']
        nodes=defaultdict(lambda:defaultdict(list))
        for name in ['node_memory','node_cpu']:
            for path in sorted((run/'capture').glob('*/captures/during/metrics/'+name+'.json')):
                for series in read(path)['data']['result']:
                    nodes[series['metric']['k8s_node_name']][name].extend(float(v) for _,v in series['values'])
        result['runs'][role]={'cpu_cores_peak':max(cpu) if cpu else None,'cpu_cores_sample_mean':sum(cpu)/len(cpu) if cpu else None,
            'heap_peak_bytes':max(heap) if heap else None,'working_set_peak_bytes':assessment['working_set_peak_bytes'],
            'queue_peaks':queues,'monitor_samples':len(rows),'first_sample_utc':rows[0]['at_utc'],'last_sample_utc':rows[-1]['at_utc'],
            'nodes':{n:{'memory_available_min_bytes':min(v['node_memory']) if v['node_memory'] else None,
                       'cpu_usage_peak_cores':max(v['node_cpu']) if v['node_cpu'] else None} for n,v in nodes.items()},
            'monitor_sha256':sha(run/'monitor.jsonl')}
    save(ROOT/'results/resource-summary.json',result)
    print(json.dumps(result))
if __name__=='__main__':main()
