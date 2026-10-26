import pathlib,json,hashlib,subprocess
P=pathlib.Path(__file__).resolve().parent
read=lambda n:json.loads((P/n).read_text())
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
s=read('source-validation.json');t=read('target-validation.json');p=read('transfer-plan.json');r=read('transfer-result.json');v=read('promotion-and-final-inventory.json');after=read('target-after.json');before=read('target-before.json')
assert v['status']=='SF001_TRANSFER_READY' and s['files']==t['files'] and all(v['files'][n]['sha256']==x['sha256'] for n,x in s['files'].items())
assert after['service_config_process_and_indexes_unchanged']
master_time=subprocess.check_output(['ssh','-S','/tmp/sfgen-master.sock','root@192.168.53.52','date -u +%Y-%m-%dT%H:%M:%SZ'],text=True).strip()
remote_evidence='/root/ops-competition-2026/transfers/SF-001-to-51-'+p['transfer_id']
record={'status':'SF001_TRANSFER_READY','case_id':'SF-001','created_at_master_utc':master_time,'source_host':'192.168.53.52','source_directory':p['source'],'target_host':'192.168.51.51','target_directory':p['final_directory'],
 'files':v['files'],'file_count':6,'total_bytes':77945722,'public_manifest_sha256':s['manifest_sha256'],'method':p['method'],'local_public_package_written':False,
 'parquet_readback':'PASS_SOURCE_AND_TARGET_FULL_DECODE','public_private_leak_check':'PASS','schemas_and_timestamp_bounds_equal':True,'atomic_no_replace':True,
 'service_config_process_indexes_unchanged':True,'target_mount':v['mount'],'target_free_bytes':v['data_disk_free_bytes'],'database_imported':False,'api_published':False,'sample_machine_accessed':False,
 'evidence_sha256':{n:sha(P/n) for n in ['source-validation.json','target-validation.json','target-before.json','target-after.json','transfer-plan.json','transfer-result.json','per-file-transfer.jsonl','promotion-and-final-inventory.json']},
 'private_files_transferred':[],'transfer_duration_seconds':r['elapsed_seconds'],'effective_bytes_per_second':r['effective_bytes_per_second'],'authorized_reader':'root; API/web users denied','master_evidence_directory':remote_evidence}
with (P/'handoff-manifest.json').open('x') as f:json.dump(record,f,indent=2)
def table(headers,rows):return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(str(x) for x in row)+' |\n' for row in rows)
text='# SF-001 Public 数据传输至比赛服务器交接报告\n\n最终状态：**SF001_TRANSFER_READY**。六个 Public 文件已传输、在目标端独立读取校验，并原子切换到暂存路径。本轮仅数据交接；数据库导入、API 发布和参赛样板机访问均未执行。\n\n'
text+='报告生成时间（Master UTC）：'+master_time+'。\n\n'
text+='## 位置与授权\n\n'+table(['项目','实际结果'],[
 ['源服务器','root@192.168.53.52'],['源目录',p['source']],['目标服务器','root@192.168.51.51（使用用户明确提供的授权账号；凭据不写入报告或文件）'],['后续 Codex 应读取的具体路径',p['final_directory']+'/'],
 ['源文件数量 / 字节','6 / 77,945,722'],['目标文件数量 / 字节',str(v['file_count'])+' / '+str(v['total_bytes'])],['Public Manifest SHA-256',s['manifest_sha256']],['目标数据盘',v['mount']['source']+' / '+v['mount']['fstype']+' / '+v['mount']['target']],['目标盘 UUID',v['mount']['uuid']],['目标盘可用空间',str(v['data_disk_free_bytes'])+' bytes（约 '+f'{v["data_disk_free_bytes"]/10**9:.3f}'+' GB）'],['目录与文件权限','root:root；目录 0700；六个文件 0600']])
text+='\n源目录没有额外文件、软链接或非普通文件。目标目录同样严格为六文件白名单。源 Manifest、大小及数据规模均与指定值一致。没有复制源父目录。\n\n'
text+='## 网络、传输方式与时间\n\nMac 到两端的授权 SSH 实际成功，目标主机使用已有 known_hosts 身份校验。Master 直连 192.168.51.51:22 的只读连接检查超时，因此采用 Mac 内存管道中继：Master → 加密 SSH → Mac 管道 → 加密 SSH → 比赛服务器。两端均正常认证，没有修改路由、SSH 配置或绕过认证。Mac 未落盘保存任何 Public 数据文件。\n\n'
text+=table(['时间/性能项目','记录'],[['传输开始（Mac 中继 UTC）',r['started_utc']],['传输完成（Mac 中继 UTC）',r['ended_utc']],['目标首个文件写入确认 UTC',r['files_transferred'][0]['received_utc']],['目标末个文件写入确认 UTC',r['files_transferred'][-1]['received_utc']],['目标原子切换完成 UTC',v['at_utc']],['传输用时（单调时钟，含准备和逐文件写入校验）',f'{r["elapsed_seconds"]:.6f} 秒'],['实际数据量',str(r['bytes_transferred'])+' bytes'],['有效平均速率',f'{r["effective_bytes_per_second"]:.2f} B/s，约 {r["effective_bytes_per_second"]/10**6:.3f} MB/s'],['源内容审计耗时',f'{s["elapsed_seconds"]:.3f} 秒'],['目标独立审计耗时',f'{t["elapsed_seconds"]:.3f} 秒']])
text+='\n各端 UTC 按原始时钟记录，不用跨机器时间戳差估算传输耗时；速率基于中继单调时钟。完整校验耗时不混入网络传输速率。\n\n'
text+='临时目录为 `'+p['temporary_directory']+'`。文件使用排他创建逐个写入，写满后 fsync、重新计算 SHA-256；每个完成文件只在哈希正确后命名。所有六文件完成独立校验后，使用 Linux renameat2(RENAME_NOREPLACE) 原子改名，绝不覆盖已存在目标。临时目录已不再存在，未留下 .part 或 READY 标记额外文件。本轮无断线或重传。\n\n'
text+='## 六文件逐项 SHA-256 比较\n\n'+table(['文件','字节','源 SHA-256','目标 SHA-256','比较'],[[n,x['bytes'],x['sha256'],v['files'][n]['sha256'],'PASS'] for n,x in s['files'].items()])
text+='\n## Parquet、Schema 与时间窗口\n\n源文件通过 SSH 只读流入 Mac 内存，在已有独立 Python 环境中校验；未运行 RCA 方法。目标端使用已有 `/opt/aiops-contest-server/.venv/bin/python`，仅运行独立 PyArrow 文件校验，不导入应用模块、不连接数据库、不安装或修改依赖。\n\n'
text+=table(['文件','实际行数','Row groups','Header/Footer','全量解码'],[[n,x['rows'],x['row_groups'],x['header_footer'],x['full_decode']] for n,x in t['files'].items() if n.endswith('.parquet')])
text+='\n源与目标的 Arrow Schema、字段可空性、UTC 纳秒时间类型和实际时间戳边界逐项一致。Metrics/Logs/Spans 均在 `[2026-10-14T09:12:25.156656Z, 2026-10-14T10:12:25.156656Z)` 内，60 个分钟计数与 Manifest 一致。119,091 个 Trace Index 与 817,529 个 Span 身份及计数完全一致；保留真实 Span 持续时间和索引结束时间，不修改原始数据。未知字段与 NULL 原样保留。\n\n'
text+=f'源 PyArrow 版本 `{s["pyarrow_version"]}`；目标 PyArrow 版本 `{t["pyarrow_version"]}`。两端全量内容检查均为 `{t["public_leak_scan"]}`，包括 Parquet 全部行和 JSON；没有把注入计划、GT 或控制记录带入六文件包。\n\n'
text+='## 私有文件排除与暂存安全\n\n明确未传输 injection-plan.json、injection-record.json、anomaly-candidates.json、anomaly-gt-draft.json、review-notes.md、私有 GT、Watchdog/Controller/Worker 调度记录及约 11 GB 原始采集目录。目标暂存目录只含六个明确列出的 Public 文件；校验代码经 SSH 内存执行，未作为第七个文件写入目标。\n\n'
text+='Nginx 当前只监听 127.0.0.1:8080 并代理本机 API；API 只监听 127.0.0.1:8000。实际 API 使用 aiops-api 用户，数据访问根仅为 public/mock-only，不扫描 staging。staging 为 root:root 0700。已实际验证 aiops-api 与 www-data 对目标 metrics.parquet 读取被拒绝，而已授权 root 可读取。没有改变现有权限配置、发布 Manifest 或开放目录给参赛者。\n\n'
text+='## 服务与数据保护验证\n\n传输前后 API/Nginx 配置及应用代码 SHA-256、服务 MainPID/启动时间和索引文件 inode/大小/mtime 完全相同。目标 public/cases/SF-001 与 public/manifests/SF-001.json 均未创建；原有 staging 样例保持原位。没有运行 import_case.py，没有连接 DuckDB，没有重启 Nginx/FastAPI 或修改比赛服务配置。未访问 192.168.51.50，未进行故障注入，未删除或修改任何 ATTEMPT 历史资产。\n\n'
text+='## 交接\n\n下一阶段比赛服务器 Codex 应通过已授权管理员权限读取：\n\n`root@192.168.51.51:/data/aiops-contest/staging/SF-001-incoming/`\n\n本轮只完成传输；下一阶段可依据 READY 状态开始单独授权的导入工作，不把本状态误解为已导入或已发布。\n\n'
text+='Master 证据目录：`'+remote_evidence+'`。`handoff-manifest.json` 为最终交接状态；transfer-result.json 保留“写入完成、等待独立校验”的中间终态，后续目标校验与原子切换记录完整保留，不覆盖过程记录。\n\n'
text+='Master 报告：`/root/ops-competition-2026/reports/SF-001-transfer-to-51-report.md`。\n'
with (P/'SF-001-transfer-to-51-report.md').open('x') as f:f.write(text)
print(json.dumps({'status':record['status'],'master_evidence_directory':remote_evidence,'report_sha256':sha(P/'SF-001-transfer-to-51-report.md'),'handoff_manifest_sha256':sha(P/'handoff-manifest.json')}))
