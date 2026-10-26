#!/usr/bin/env python3
"""Preserve SF-008's failed attempts and capture-429 terminal in the ledger."""

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


OPS = Path('/root/ops-competition-2026')
DATA = Path('/data/stage5-v3/ops-competition-2026')
LEDGER = OPS/'plans/SF-003-to-SF-012-production-v2-ledger.json'
RUN_ID = 'SF-008-ATTEMPT-04'
RUN = DATA/'runs'/RUN_ID
INDEX = Path('/data/stage5-v3/ops-competition-archive-index')/RUN_ID
REAL = OPS/'sf008-real-adapter-v4-20261016'
EXPECTED_SHA = '87cb426415eae9bf7d0a9229b72043919e9181bb537ef14ffa74b9c6a2efe5f4'


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024**2), b''):
            checksum.update(block)
    return checksum.hexdigest()


def read(path):
    return json.loads(path.read_text())


def require(condition, reason):
    if not condition:
        raise RuntimeError(reason)


if __name__ == '__main__':
    require(sha(LEDGER) == EXPECTED_SHA, 'Ledger changed before update')
    ledger = read(LEDGER)
    cases = [row for row in ledger['official_cases'] if row['case_id']=='SF-008']
    require(len(cases)==1 and cases[0]['status']=='NOT_STARTED',
            'Previous SF-008 state differs')
    capture = read(RUN/'terminal.json')
    fault = read(RUN/'control/fault-controller-terminal.json')
    controls = read(RUN/'controls/result-manifest.json')['streams']
    stderr = (RUN/'chunk-05-first.stderr').read_text()
    require(capture['status']=='SF008_FORMAL_CAPTURE_FAILED' and
            capture['public_created'] is False and
            capture['private_gt_created'] is False and
            "chunk-05-first" in capture['error'] and
            'HTTP Error 429: Too Many Requests' in stderr,
            'Capture failure is not the registered OpenSearch 429')
    require(fault['status']=='SF008_FAULT_SEQUENCE_COMPLETE_PENDING_CAPTURE' and
            len(fault['faults'])==1 and fault['stages']['pod_recovered'] is True and
            fault['stages']['http_delay']['delayed_home_requests']>=60,
            'Fault execution or recovery differs')
    require(all(controls[name]['planned_requests']==count and
                controls[name]['successful_requests']==count and
                controls[name]['missed_arrivals']==0
                for name,count in (('image_asset',1800),('cart_view',3600))),
            'Control workload incomplete')
    require(not (DATA/'packages'/RUN_ID).exists(),
            'Failed capture unexpectedly has an export package')
    source_manifest = INDEX/'source-manifest.json'
    source = read(source_manifest)
    require(source['file_count']==2250 and source['bytes']==4_840_279_825,
            'Failed raw source inventory differs')
    for host in ('w1','w2'):
        receipt = read(INDEX/(host+'-verification.json'))
        require(receipt['status']=='COMPETITION_ARCHIVE_SHA_AND_RESTORED_READ_PASS' and
                receipt['source_manifest_sha256']==sha(source_manifest) and
                receipt['bytes']==source['bytes'],
                'Failed raw archive verification on '+host)
    residue = read(REAL/'post-archive-residue.json')
    competition = read(REAL/'post-archive-competition-residue.json')
    storage = read(REAL/'post-archive-storage.json')
    require(residue['status']=='HOST_RESIDUE_PASS' and
            competition['status']=='COMPETITION_UNIT_RESIDUE_PASS' and
            storage['one_case']['status']=='DYNAMIC_STORAGE_PASS',
            'Final safety gate failed')
    attempt01 = read(DATA/'runs/SF-008-ATTEMPT-01/control/fault-controller-terminal.json')
    attempt02 = read(DATA/'runs/SF-008-ATTEMPT-02/control/five-host-storage.json')
    require(attempt01['status']=='SF008_FORMAL_SEQUENCE_FAILED' and
            'SF008_RUN' in attempt01['error'] and
            attempt02['status']=='FIVE_HOST_STORAGE_HEARTBEAT_FAILED',
            'Earlier pre-injection attempts differ')
    previous = copy.deepcopy(cases[0])
    updated = {
        'case_id':'SF-008', 'status':'FAILED_CAPTURE_INFRASTRUCTURE',
        'run_id':RUN_ID,
        'faults':['POD_MEMORY_32M_60','HTTP_HOME_DELAY_250MS_60'],
        'fault_count':2, 'failed_gate':'JAEGER_OPENSEARCH_TRACE_SCROLL_HTTP_429',
        'formal_manifest_sha256':sha(REAL/'plans'/RUN_ID/'run-manifest.json'),
        'capture_terminal_sha256':sha(RUN/'terminal.json'),
        'raw_archive_manifest_sha256':sha(source_manifest),
        'raw_master':str(RUN),
        'raw_worker1':'/data/rca-archives/ops-competition-2026/'+RUN_ID+'/raw',
        'raw_worker2':'/data/rca-archives/ops-competition-2026/'+RUN_ID+'/raw',
        'public_package':'NOT_CREATED',
        'gt_status':'NOT_CREATED_FAILED_CAPTURE',
        'server_import':'PROHIBITED_FOR_FAILED_ATTEMPT',
        'attempt_history':[previous,
            {'run_id':'SF-008-ATTEMPT-01', 'status':'FAILED_BEFORE_INJECTION',
             'reason':'Missing SF008_RUN in ARMED preflight'},
            {'run_id':'SF-008-ATTEMPT-02', 'status':'FAILED_BEFORE_INJECTION',
             'reason':'Five-host monitor used an obsolete 30-second capture startup grace'},
            {'run_id':'SF-008-ATTEMPT-03', 'status':'FAILED_BEFORE_INJECTION',
             'reason':'Old observer remained active; original residue audit missed ops-sf008 prefix'},
        ],
    }
    ledger['official_cases'][ledger['official_cases'].index(cases[0])] = updated
    ledger['completed_ready_for_import'] = sum(
        row['status']=='READY_FOR_IMPORT' for row in ledger['official_cases'])
    ledger['remaining'] = len(ledger['official_cases'])-ledger['completed_ready_for_import']
    ledger.setdefault('status_changes',[]).append({
        'case_id':'SF-008','from_status':previous['status'],
        'to_status':updated['status'],'run_id':RUN_ID,
        'at_utc':datetime.now(timezone.utc).isoformat(),
        'previous_ledger_sha256':EXPECTED_SHA,
        'capture_terminal_sha256':updated['capture_terminal_sha256'],
    })
    backup = OPS/'plans/history'/('SF-003-to-SF-012-v2-ledger-before-'+RUN_ID+'.json')
    backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with backup.open('xb') as stream:
        stream.write(LEDGER.read_bytes())
    backup.chmod(0o600)
    require(sha(backup)==EXPECTED_SHA and sha(LEDGER)==EXPECTED_SHA,
            'Ledger changed during backup')
    with tempfile.NamedTemporaryFile('w',dir=LEDGER.parent,delete=False,
                                     prefix='.sf008-ledger-',suffix='.json') as stream:
        json.dump(ledger,stream,indent=2,ensure_ascii=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
        pending=Path(stream.name)
    pending.chmod(0o600)
    require(sha(LEDGER)==EXPECTED_SHA,'Ledger changed before atomic replace')
    os.replace(pending,LEDGER)
    print(json.dumps({'status':'FAILED_CAPTURE_RECORDED',
                      'ready':ledger['completed_ready_for_import'],
                      'remaining':ledger['remaining'],
                      'ledger_sha256':sha(LEDGER)}))
