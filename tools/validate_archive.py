#!/usr/bin/env python3
"""Offline archive validation only. No project import, remote read or fault execution."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re
import sys
from datetime import datetime

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path)
    args=parser.parse_args()
    errors=[];source_count=0;changed=[];seen=set()
    for name in ('server51','master','organizer50'):
        manifest=json.loads((ROOT/'provenance'/f'{name}.json').read_text())
        if manifest['missing_sources']:errors.append(f'{name}: missing sources')
        for item in manifest['files']:
            rel=item['path'];p=ROOT/rel
            if rel in seen:errors.append('Duplicate provenance: '+rel)
            seen.add(rel)
            if not p.is_file() or p.is_symlink():errors.append('Missing or linked source: '+rel);continue
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            if digest!=item['sha256']:errors.append('Archive hash mismatch: '+rel)
            if item['source_sha256']!=item['sha256']:
                if not item['sanitization']:errors.append('Unexplained source modification: '+rel)
                changed.append(rel)
            source_count+=1
    py_count=0;json_count=0
    banned={'.db','.sqlite','.sqlite3','.parquet','.xlsx','.xls','.pem','.key','.tgz','.gz','.zip','.jsonl','.pyc'}
    for p in ROOT.rglob('*'):
        if '.git' in p.parts or not p.is_file():continue
        rel=p.relative_to(ROOT).as_posix()
        if p.is_symlink():errors.append('Unexpected symlink: '+rel)
        if p.suffix in banned:errors.append('Excluded file type: '+rel)
        if p.name in {'team.env','agent-llm.env','id_rsa','id_ed25519','known_hosts','authorized_keys'}:
            errors.append('Excluded credential file: '+rel)
        if p.suffix=='.py':
            try:ast.parse(p.read_bytes(),filename=rel);py_count+=1
            except (SyntaxError,ValueError) as exc:errors.append(f'Python syntax {rel}: {exc}')
        if p.suffix=='.json':
            try:json.loads(p.read_text());json_count+=1
            except Exception as exc:errors.append(f'JSON {rel}: {exc}')
    catalog=json.loads((ROOT/'questions/semifinal/catalog-deployed.json').read_text())
    questions=catalog['questions']
    semi=[q for q in questions if q['mode']=='semifinal']
    if {q['question_id'] for q in semi}!={f'Q{i:02}' for i in range(1,11)}:errors.append('Semifinal question IDs differ')
    if any(len(q.get('three_questions',q.get('questions',[])))!=3 for q in questions):errors.append('Question without exactly three subquestions')
    official_qa=list((ROOT/'private/ground-truth/semifinal').glob('Q*.json'))
    if len(official_qa)!=10:errors.append('Expected ten deployed semifinal GT files')
    if any(json.loads(p.read_text()).get('review_status')!='APPROVED' for p in official_qa):errors.append('Deployed semifinal GT not APPROVED')
    official_final=list((ROOT/'private/ground-truth/final').glob('SF-*.json'))
    if len(official_final)!=12:errors.append('Expected twelve deployed final GT files')
    final_manifest=json.loads((ROOT/'private/ground-truth/final/gt-manifest.json').read_text())
    if final_manifest.get('review_status')!='APPROVED':errors.append('Final GT manifest not APPROVED')
    for item in final_manifest['cases']:
        p=ROOT/'private/ground-truth/final'/item['file']
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest()!=item['sha256']:
            errors.append('Final approved manifest hash mismatch: '+item['case_id'])
    if any(json.loads(p.read_text()).get('review_status')!='APPROVED' for p in official_final):
        errors.append('Deployed final GT not APPROVED')
    final=json.loads((ROOT/'questions/final/task-catalog.json').read_text())
    tasks={t['task_id']:t['case_id'] for t in final['tasks']}
    expected={f'FT-{i:02}' for i in range(1,4)}|{f'PK-R{i:02}' for i in range(1,10)}
    if set(tasks)!=expected:errors.append('Final task catalog differs from FT3 plus PK9')
    if set(tasks.values())!={p.stem for p in official_final}:errors.append('Final GT/case mappings differ')
    event=json.loads((ROOT/'questions/final/event-plan.json').read_text())
    if event['pk_tasks']!={k:v for k,v in tasks.items() if k.startswith('PK-')}:errors.append('PK event/task mappings differ')
    cases=list((ROOT/'datasets/public-catalog').glob('SF-*/manifest.json'))
    for p in cases:
        d=json.loads(p.read_text())
        if d['case_id']!=p.parent.name:errors.append('Public manifest/case mismatch: '+str(p))
    schema_count=0;schema_status='NOT_RUN_JSONSCHEMA_NOT_INSTALLED'
    try:
        from jsonschema import Draft202012Validator
        generation=ROOT/'questions/semifinal/generation'
        for folder,schema in (
            ('public/questions','public/schemas/question.schema.json'),
            ('public/practice-examples','public/schemas/question.schema.json'),
            ('private/ground-truth','private/schemas/gt.schema.json')):
            validator=Draft202012Validator(json.loads((generation/schema).read_text()))
            for p in (generation/folder).glob('*.json'):
                try:validator.validate(json.loads(p.read_text()));schema_count+=1
                except Exception as exc:errors.append(f'Schema {p.relative_to(ROOT)}: {type(exc).__name__}')
        schema_status='PASS'
    except ImportError:pass
    report={'status':'PASS' if not errors else 'FAIL','source_files_verified':source_count,
            'sanitized_source_files':changed,'python_files_parsed':py_count,'json_files_parsed':json_count,
            'registered_questions':len(questions),'formal_semifinal_questions':len(semi),
            'formal_semifinal_gt':len(official_qa),'formal_final_gt':len(official_final),'final_tasks':len(tasks),
            'public_case_manifests':len(cases),'generated_question_gt_schema_checks':schema_count,
            'schema_validation':schema_status,'raw_parquet_validated':False,'faults_executed':0,
            'live_services_modified':False,'errors':errors}
    if args.report:
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return bool(errors)

if __name__=='__main__':sys.exit(main())
