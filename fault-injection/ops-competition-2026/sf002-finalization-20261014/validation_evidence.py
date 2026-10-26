"""Append explicit PASS/FAIL/UNKNOWN evidence links without changing original assessments."""
import json,hashlib
from pathlib import Path
R=Path('/data/stage5-v3/ops-competition-2026/batch-sf002-sf009/SF-002')
def read(p):return json.loads(p.read_text())
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(4*1024**2),b''):h.update(b)
 return h.hexdigest()
def main():
 v=read(R/'private/fault-validation-report.json');m=read(R/'public/manifest.json');proof=read(R/'reports/public-readback-verification.json')
 assert proof['status']=='PUBLIC_FULL_READBACK_SCHEMA_IDENTITY_HASH_LEAK_PASS'
 mappings={'INJECTION_CONFIRMED':'Injection Success','TARGET_CONFIRMED':'Target Correctness','INTENSITY_CONFIRMED':'Fault Intensity','OBSERVABLE':'Observability','RECOVERY_CONFIRMED':'Recovery','TELEMETRY_QUALITY_PASS':'Telemetry Quality'}
 for e in v['events']:
  public_files=['metrics.parquet','traces.parquet','topology.json']
  e['public_evidence_files']=[{'file':n,**m['files'][n]} for n in public_files]
  e['private_execution_reference']={'file':'private/injection-record.json','sha256':sha(R/'private/injection-record.json'),'json_pointer':'/faults/'+e['action_id']}
  e['assessment_reference']={'file':'capture-and-effects-assessment.json','sha256':sha(R/'capture-and-effects-assessment.json'),'json_pointer':'/service_effect' if e['target_layer']=='SERVICE' else '/pod_effect'}
  e['required_verdicts']={n:{'status':'PASS' if e['checks'][k] else 'FAIL','evidence':['assessment_reference','private_execution_reference'] if n in ('INJECTION_CONFIRMED','TARGET_CONFIRMED','INTENSITY_CONFIRMED','RECOVERY_CONFIRMED') else ['public_evidence_files','assessment_reference']} for n,k in mappings.items()}
  impacted=e['target_layer']=='SERVICE' and e['user_impact']['during']['failed']>0
  e['required_verdicts']['USER_IMPACT_CONFIRMED']={'status':'PASS' if impacted else 'UNKNOWN','evidence':['public_evidence_files','assessment_reference','user_impact'],'reason':'Actual failed requests are matched to public traces.' if impacted else 'No request failures observed in this event window; absence of all latency/user impact is not established. No post-hoc impact threshold is introduced.'}
  e['user_impact_determines_admission']=False
 v.update(status='EVIDENCE_LINKED_MECHANISM_VALIDATION',version=2,prior_version_sha256=sha(R/'private/fault-validation-report.json'),public_manifest_sha256=sha(R/'public/manifest.json'),gt_draft_uses_these_private_windows=False)
 with (R/'private/fault-validation-report-v2.json').open('x') as f:json.dump(v,f,indent=2)
 text='# SF-002 故障机制实证验证\n\n原始 v1 结果保留；v2 补充逐维度 PASS/FAIL/UNKNOWN 与公私证据引用，不修改注入、采集或准入规则。\n\n'
 for e in v['events']:
  text+='## '+e['fault_event_id']+' / '+e['fault_type']+'\n\n'
  text+='综合状态：`'+e['mechanism_validation_status']+'`。\n\n'
  for name,item in e['required_verdicts'].items():text+='- '+name+'：'+item['status']+'\n'
  text+='\n用户请求统计：`'+json.dumps(e['user_impact'],ensure_ascii=False)+'`。\n\n'
  text+='公开异常证据：`'+json.dumps(e['public_effect'],ensure_ascii=False)+'`。\n\n'
  text+='证据引用及 SHA-256：private/fault-validation-report-v2.json。执行依据仅用于机制核验；异常 GT 候选由 Public-only 分析独立生成。\n\n'
 text+='正常对照和安全检查通过只覆盖已登记检查范围，不等于证明所有非目标实体均无任何影响。Pod 未产生请求失败不阻止资源异常进入半决赛题目；未事后添加用户影响阈值。\n'
 with (R/'reports/fault-validation-report.md').open('x') as f:f.write(text)
 print(json.dumps({'events':len(v['events']),'status':v['status']}))
if __name__=='__main__':main()
