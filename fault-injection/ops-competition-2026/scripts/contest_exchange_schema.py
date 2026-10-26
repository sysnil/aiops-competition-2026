#!/usr/bin/env python3
"""Lossless identity/time normalization helpers; no GT, injector or model inputs."""
from datetime import datetime,timezone
import json
import re

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def iso_ns(value):
    match=re.fullmatch(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})',value)
    if not match:raise ValueError('Unparseable precise timestamp')
    second=datetime.fromisoformat(match[1]+match[3].replace('Z','+00:00'))
    return int(second.timestamp())*1_000_000_000+int((match[2] or '').ljust(9,'0'))

def trace_id(value,width):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-fA-F]{'+str(width)+'}',value):
        raise ValueError('Invalid original trace/span identity')
    return value  # preserve original representation, including case

def tags(rows):
    result={}
    for row in rows or []:
        key=row.get('key')
        if key in result and result[key]!=row.get('value'):raise ValueError('Conflicting duplicate attribute key')
        result[key]=row.get('value')
    return result

def span_attributes(span):
    output=tags(span.get('tags'))
    for key,value in (span.get('tag') or {}).items():
        key=key.replace('@','.')
        if key not in output:output[key]=value
    return output

def span_status(attributes):
    explicit=attributes.get('otel.status_code')
    if explicit is not None:return str(explicit)
    if attributes.get('error') in (True,'true',1):return 'ERROR'
    # Absence of an error tag is not evidence of an explicit OK status.
    return 'UNSET'

def trace_record(span,parent_service):
    attrs=span_attributes(span);parents=[r for r in span.get('references',[]) if r.get('refType')=='CHILD_OF']
    if len(parents)>1:raise ValueError('Multiple CHILD_OF parents require explicit schema extension')
    parent=parents[0].get('spanID') if parents else None
    return {'trace_id':trace_id(span['traceID'],32),'span_id':trace_id(span['spanID'],16),
        'parent_span_id':trace_id(parent,16) if parent else None,'parent_service':parent_service,
        'service':span['process']['serviceName'],'operation_name':span['operationName'],
        'start_time':int(span['startTime'])*1000,'duration_ms':span['duration']/1000,
        'status':span_status(attrs),'attributes':canonical(attrs),
        'resource_attributes':canonical(tags(span['process'].get('tags'))),
        'references':canonical(span.get('references',[])),'span_logs':canonical(span.get('logs',[])),
        'flags':span.get('flags')}

def log_record(source):
    resource=source.get('resource') or {};attributes=source.get('attributes') or {}
    severity=source.get('severity')
    level=severity.get('text') if isinstance(severity,dict) else severity
    # Only the source's explicit level is used; no keyword classification.
    return {'timestamp':iso_ns(source['@timestamp']),'service':resource.get('service.name'),
        'pod':resource.get('k8s.pod.name'),'severity':str(level) if level not in (None,'') else 'UNKNOWN',
        'body':source.get('body') if isinstance(source.get('body'),str) else canonical(source.get('body')),
        'attributes':canonical(attributes),'trace_id':source.get('traceId') or None,
        'span_id':source.get('spanId') or None,'resource_attributes':canonical(resource),
        'severity_original':canonical(severity),'instrumentation_scope':canonical(source.get('instrumentationScope')),
        'observed_timestamp_original':canonical(source.get('observedTimestamp'))}

def private_leaks(row):
    serialized=canonical(row)
    forbidden=('SF-PILOT-01','COMP-SF01-','/root/ops-competition-2026','/data/stage5-v3/ops-competition-2026',
        'private_injection','injection-plan','anomaly-gt','SIGSTOP','resource_memory_holder.py')
    return [s for s in forbidden if s in serialized]

def schemas(pa):
    text=pa.string();clock=pa.timestamp('ns',tz='UTC')
    return {
      'metrics':pa.schema([('timestamp',clock),('metric_name',text),('value',pa.float64()),('labels',text),
        ('service',text),('pod',text),('node',text),('unit',text),('metric_type',text),('source',text)]),
      'logs':pa.schema([(k,clock if k=='timestamp' else text) for k in ('timestamp','service','pod','severity','body',
        'attributes','trace_id','span_id','resource_attributes','severity_original','instrumentation_scope','observed_timestamp_original')]),
      'traces':pa.schema([(k,clock if k=='start_time' else pa.float64() if k=='duration_ms' else pa.int64() if k=='flags' else text)
        for k in ('trace_id','span_id','parent_span_id','parent_service','service','operation_name','start_time',
          'duration_ms','status','attributes','resource_attributes','references','span_logs','flags')])}
