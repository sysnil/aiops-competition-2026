"""Parquet roundtrip of real readiness observations; not a competition dataset."""
import json,hashlib
from decimal import Decimal
from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq
from pilot_exchange_schema import schemas,trace_record,log_record,private_leaks,iso_ns
def main():
    root=Path('/data/ops-competition-2026/attempt02-schema-smoke');fixture=json.loads((root/'input.json').read_text())
    assert fixture['fixture'] and fixture['formal_case_created'] is False
    expected={'traces':[trace_record(s,None) for s in fixture['spans']], 'logs':[log_record(s) for s in fixture['logs']]}
    expected['metrics']=[{'timestamp':int(Decimal(str(t))*10**9),'metric_name':series['metric']['__name__'],
        'value':float(v),'labels':json.dumps(series['metric'],sort_keys=True),'service':None,'pod':series['metric'].get('pod'),
        'node':series['metric'].get('kubernetes_io_hostname'),'unit':None,'metric_type':None,'source':'PROMETHEUS'}
        for series in fixture['metrics'] for t,v in series['values']]
    result={'status':'REAL_OBSERVATION_PARQUET_SCHEMA_SMOKE_PASS','fixture_only':True,'formal_case_created':False,'pyarrow':pa.__version__,'checks':{}}
    for kind,rows in expected.items():
        assert not any(private_leaks(r) for r in rows)
        file=root/(kind+'.parquet')
        assert not file.exists()
        table=pa.Table.from_pylist(rows,schema=schemas(pa)[kind]);pq.write_table(table,file,compression='zstd')
        restored=pq.read_table(file);field='start_time' if kind=='traces' else 'timestamp'
        assert restored[field].cast(pa.int64()).to_pylist()==[r[field] for r in rows]
        assert restored.schema==table.schema and restored.num_rows==len(rows)
        if kind=='metrics':assert restored['value'].to_pylist()==[r['value'] for r in rows]
        if kind=='traces':
            assert restored['trace_id'].to_pylist()==[s['traceID'] for s in fixture['spans']]
            assert restored['span_id'].to_pylist()==[s['spanID'] for s in fixture['spans']]
        result['checks'][kind]={'rows':len(rows),'schema':str(restored.schema),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'nanoseconds_preserved':True}
    with (root/'result.json').open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps(result))
if __name__=='__main__':main()
