"""Validate candidate schemas and assemble a complete private review report."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from question_specs import QUESTIONS, PRACTICE


ROOT = Path('/root/ops-competition-2026/semifinal-qa-benchmark-v1')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def polish_public_and_review(root):
    question_manifest_path = root / 'public/question-manifest.json'
    question_manifest = json.loads(question_manifest_path.read_text())
    if 'questions' in question_manifest:
        entries = question_manifest.pop('questions')
        question_manifest['formal_questions'] = [row for row in entries
            if row['classification'] == 'FORMAL_CANDIDATE']
        question_manifest['practice_examples'] = [row for row in entries
            if row['classification'] == 'PRACTICE_EXAMPLE']
    if len(question_manifest['formal_questions']) != 10 or \
            len(question_manifest['practice_examples']) != 2:
        raise ValueError('Formal/practice question separation failed')
    manual = ['# 选手手册：查询与绘图练习示例', '',
        '仅使用 SF-001、SF-002 的练习 Public 数据；这两题已给出答案，'
        '不属于正式 Q01～Q10。',
        '实际 Question API 尚未在本任务中核验；按题面所列的 Public 字段过滤、'
        '分桶和绘图，不虚构接口 URL。', '']
    committee = ['# 组委会逐题审核表', '',
                 '正式题每题 1 张图、3 问、10 分；本文件及答案留在 Private。', '']
    for spec in QUESTIONS + PRACTICE:
        folder = 'practice-examples' if spec in PRACTICE else 'questions'
        qpath = root / 'public' / folder / f"{spec['id']}.json"
        gtpath = root / 'private/ground-truth' / f"{spec['id']}-gt.json"
        question = json.loads(qpath.read_text())
        gt = json.loads(gtpath.read_text())
        question['query_spec']['kind'] = spec['kind']
        question['query_spec']['answer_plan'] = spec['answer_plan']
        if spec['kind'] in ('server_span_count', 'error_log_count'):
            noun = 'ERROR 日志' if spec['kind'] == 'error_log_count' else '服务端 Span'
            question['questions'][0]['text'] = f'整段时间内共有多少条符合条件的 {noun}？'
            question['questions'][1]['text'] = f'数量最多的一分钟有多少条 {noun}？'
        qpath.write_text(json.dumps(question, ensure_ascii=False, indent=2) + '\n')
        if spec in PRACTICE:
            missing_note = ('没有符合条件日志的分钟记为 0。' if spec['kind'] == 'error_log_count'
                            else '采样不足的分钟保留为空值，不补零。')
            manual += [f"## {spec['id']} · {spec['case']} · {question['title']}", '',
                f"实体：{', '.join(spec['entities'])}；数据：{question['query_spec']['source_file']}；"
                f"窗口：{question['time_range']['start_utc']} 至 "
                f"{question['time_range']['end_exclusive_utc']}（右端不含）。", '',
                f"口径：`{question['query_spec']['aggregation']}`；"
                f'每个时间点为 60 秒桶起点。{missing_note}', '',
                f"![{spec['id']} 参考图](manual-assets/{spec['id']}.png)", '']
            for index, (prompt, gold) in enumerate(zip(question['questions'],
                                                        gt['three_gold_answers']), 1):
                unit = '条' if gold['answer_unit'] == 'logs' else gold['answer_unit']
                manual.append(f"{index}. {prompt['text']} **{gold['answer']} "
                              f"{unit}**。")
            manual += ['', '把符合过滤条件的记录按 60 秒分桶，计算各分钟数值，'
                       '从同一条时序曲线读取答案；时间题使用 UTC 桶起点。', '']
        else:
            committee += [f"## {spec['id']} · {question['title']}", '',
                f"Case {spec['case']}；目标 {', '.join(spec['entities'])}；"
                f"窗口 {question['time_range']['start_utc']} 至 "
                f"{question['time_range']['end_exclusive_utc']} UTC。", '']
            for index, (prompt, gold) in enumerate(zip(question['questions'],
                                                        gt['three_gold_answers']), 1):
                committee.append(f"{index}. {prompt['text']} **{gold['answer']} "
                                 f"{gold['answer_unit']}**；计算：{gold['calculation_method']}。")
            committee += ['', f"![参考图](reference-charts/{spec['id']}.png)", '',
                          '审核意见：____________________', '']
        section = 'practice_examples' if spec in PRACTICE else 'formal_questions'
        entry = next(row for row in question_manifest[section]
                     if row['question_id'] == spec['id'])
        entry['question_sha256'] = sha(qpath)
    question_manifest_path.write_text(json.dumps(question_manifest,
        ensure_ascii=False, indent=2) + '\n')
    chapter = '\n'.join(manual)
    (root / 'public/PLAYER_MANUAL_QUERY_EXAMPLES.md').write_text(chapter)
    (root / 'public/PLAYER_MANUAL.md').write_text('\n'.join([
        '# 院智能运维技能大赛：半决赛选手手册（查询绘图任务增补草案）',
        '',
        '本增补草案尚未发布到选手环境。正式题每题绘制一张指定图表，回答三个问题：'
        '图表 4 分，每问 2 分，每题共 10 分。',
        '只使用题目提供的 Public Metrics、Logs、Traces、Metadata 与 Topology；'
        '不需要推断故障注入类型或根因。',
        '',
        '请严格按题面给出的实体、时间窗口、过滤字段、60 秒分桶口径和单位处理数据。'
        '时间均为 UTC，窗口左闭右开；计数题没有匹配记录的分钟为 0，'
        '均值与 p95 样本不足的分钟为空值。',
        '当前 Question API 的实际端点尚未完成本轮联调，正式接入时以发布的接口文档为准。',
        '', chapter,
    ]))
    (root / 'private/COMMITTEE_REVIEW.md').write_text('\n'.join(committee))


def main(root=ROOT):
    result = json.loads((root / 'validation/reproducibility-results.json').read_text())
    if result['status'] != 'READY_FOR_HUMAN_REVIEW' or len(result['checks']) != 12 or \
            any(row['status'] != 'PUBLIC_RECOMPUTATION_PASS' for row in result['checks']):
        raise ValueError('Independent Public recomputation is incomplete')
    polish_public_and_review(root)
    question_schema = json.loads((root / 'public/schemas/question.schema.json').read_text())
    gt_schema = json.loads((root / 'private/schemas/gt.schema.json').read_text())
    check_question = Draft202012Validator(question_schema, format_checker=FormatChecker())
    check_gt = Draft202012Validator(gt_schema, format_checker=FormatChecker())
    inventory = json.loads((root / 'validation/source-inventory.json').read_text())
    lines = ['# 半决赛查询绘图候选题审核报告', '',
        '**状态：READY_FOR_HUMAN_REVIEW；10 道正式候选题、2 道练习示例。**',
        '正式 GT 均为 `DRAFT_PENDING_HUMAN_REVIEW`，未导入比赛服务器或向选手发布。',
        '', '## 源数据与质量', '',
        '| Case | 用途 | 冻结 Public 根目录 | 窗口 UTC | Manifest SHA-256 | Metrics / Logs / Spans |',
        '| --- | --- | --- | --- | --- | --- |']
    for case in ('SF-001', 'SF-002', 'SF-003', 'SF-005', 'SF-010'):
        item = inventory[case]
        role = '练习示例' if case in ('SF-001', 'SF-002') else '正式候选题'
        counts = item['rows']
        lines.append(f"| {case} | {role} | `{item['root']}` | "
            f"{item['window']['start_utc']} 至 {item['window']['end_utc']} | "
            f"`{item['manifest_sha256']}` | {counts['metrics.parquet']:,} / "
            f"{counts['logs.parquet']:,} / {counts['traces.parquet']:,} |")
    lines += ['', '五套 Public Manifest 与其声明的五个文件均已逐项验证 SHA-256 和大小。'
              '题目生成和独立复算仅读 Public；Private 注入计划未用于构造答案。',
              '', '## 统计口径与评分', '',
              '- 时间桶从 Case 公布的精确起点开始，每桶 60 秒，左闭右开，图上标 UTC 桶起点。',
              '- 内存曲线是 `container_memory_working_set_bytes` 的分钟均值，单位 MiB；CPU Counter 用相邻非负差分除以秒数，异常重置不参与，单位 core。',
              '- 服务数量题统计归档 `span.kind=server` 的 Span，属于**已观测调用数**，不宣称等于全量业务请求。',
              '- p95 在同一分钟至少 20 个有效 Server Span 时取最近秩（升序第 `ceil(0.95*n)` 个）；不足时为 null，不补零。',
              '- ERROR 日志按级别精确匹配；错误调用边按 Server Span `status=ERROR` 计数。计数题空桶为零，其他题缺样本为 null。',
              '- 每题图表 4 分（实体过滤、时间轴、数值口径、单位/图例各 1 分）；三问每题 2 分，总计 10 分。整数容差 0；MiB/ms 数值容差 0.02；CPU core 容差 0.0002；时间题 UTC 桶起点容差 0 秒。',
              '', '## 十道正式题', '',
              '| 题号 | Case | 题目 | 目标 | 数据与图 | 难度 |',
              '| --- | --- | --- | --- | --- | --- |']
    for spec in QUESTIONS:
        lines.append(f"| {spec['id']} | {spec['case']} | {spec['title_zh']} | "
                     f"{', '.join(spec['entities'])} | {spec['kind']}，1 张 60 秒折线图 | "
                     f"{spec['difficulty']} |")
    lines += ['', '难度分布：' + ', '.join(f'{key} {count}' for key, count in
        sorted(Counter(spec['difficulty'] for spec in QUESTIONS).items())) + '。',
        '每题完整 Public JSON 位于 `public/questions/`；以下答案仅供组委会内部审核。', '']

    for spec in QUESTIONS + PRACTICE:
        folder = 'practice-examples' if spec in PRACTICE else 'questions'
        qpath = root / 'public' / folder / f"{spec['id']}.json"
        gtpath = root / 'private/ground-truth' / f"{spec['id']}-gt.json"
        question, gt = json.loads(qpath.read_text()), json.loads(gtpath.read_text())
        check_question.validate(question)
        check_gt.validate(gt)
        if question['question_id'] != gt['question_id'] or question['case_id'] != gt['case_id']:
            raise ValueError(f"{spec['id']}: question/GT identity mismatch")
        lines += [f"### {spec['id']} · {question['title']}", '',
            f"Case `{question['case_id']}`；目标 `{question['target_entity_id']}`；"
            f"模态 {question['modality']}；窗口 "
            f"`{question['time_range']['start_utc']}` 至 "
            f"`{question['time_range']['end_exclusive_utc']}`（右端不含）。", '',
            f"绘图：{question['chart_requirement']['type']}，横轴 "
            f"{question['chart_requirement']['x_axis']}，纵轴 "
            f"{question['chart_requirement']['y_axis']}；"
            f"查询过滤 `{question['query_spec']['filters']}`，"
            f"聚合 `{question['query_spec']['aggregation']}`。", '']
        for index, (prompt, gold) in enumerate(zip(question['questions'],
                                                    gt['three_gold_answers']), 1):
            lines.append(f"{index}. {prompt['text']} **{gold['answer']} {gold['answer_unit']}**。"
                         f"计算：{gold['calculation_method']}；容差 "
                         f"{gold['numerical_tolerance']}，时间容差 "
                         f"{gold['time_tolerance_seconds']} 秒。")
        relative = gt['reference_chart_path']
        lines += ['', f'![{spec["id"]} 参考图]({relative})', '',
                  f'真实逐分钟绘图数据：`private/ground-truth/{spec["id"]}-gt.json` 的 '
                  '`canonical_chart_data`；同文件保留真实 Public 记录样本和评分细则。', '']

    lines += ['## 可回答性与独立复算', '',
        '12/12 题目均通过第二套独立 PyArrow 分批求解：每个图表点的 UTC、实体、样本数和数值均与 GT 一致；36/36 个答案复算一致。'
        '每张 PNG 可打开，10 道正式题均有恰好三问，分值均为 10。',
        '具体机器结果位于 `validation/reproducibility-results.json`。'
        '源指标与分钟覆盖盘点位于 `validation/source-inventory.json` 和 `validation/spec-dry-run.json`。',
        '', '## Public / Private 边界与导入说明', '',
        '- `public/questions/Q01.json` 至 `Q10.json` 只含题面、实体、时间窗、数据过滤、绘图要求与三问；不含答案、GT、注入类型。',
        '- 练习示例 `EX-P01/EX-P02` 的已解答题面与参考图只在 `public/PLAYER_MANUAL.md`、`public/PLAYER_MANUAL_QUERY_EXAMPLES.md` 和 `public/manual-assets/` 中公开。',
        '- 正式 GT、参考图与审核表只在权限 0700 的 `private/`；GT 一律保持草案状态。根目录 `sha256-manifest.json` 列出所有交付文件哈希。',
        '- Public 与 Private JSON Schema 分别位于 `public/schemas/question.schema.json`、`private/schemas/gt.schema.json`，12/12 文件已通过 Schema 校验。',
        '- 50/51 的 `SEMIFINAL_QUERY_VISUALIZATION` Question API、Demo Agent 和评分接口仍在升级；本任务未访问 50/51，因此只提供可导入数据结构，不宣称接口已适配或上线。',
        '- Master 未发现现有选手手册源文件；已生成独立的 Public 选手手册增补草案与可并入章节，待拿到正式手册源路径后再合并，当前未发布。',
        '', '人工审核前不得将任何正式 GT 设为 APPROVED，也不得把 Private 标准答案复制到 Public 包。', '']
    solver_checks = sorted((root / 'validation/reference-solvers').glob('*-check.json'))
    if solver_checks:
        if len(solver_checks) != 12 or any(json.loads(path.read_text())['status'] !=
                                          'PUBLIC_FIRST_SOLVER_PASS' for path in solver_checks):
            raise ValueError('Per-question solver set is incomplete')
        lines += ['', '## 每题求解入口', '',
                  '12/12 个 `validation/reference-solvers/<ID>.py` 已独立运行通过。'
                  '每个入口先写出 Public-only 解题结果和独立图表，之后才读取 Private GT 比对；'
                  '各自的 `*-public-solution.json`、`*-check.json` 和图表均保留。', '']
    (root / 'REPORT.md').write_text('\n'.join(lines))
    hashes = {}
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.name != 'sha256-manifest.json' and \
                not path.is_relative_to(root / 'validation') and \
                not path.is_relative_to(root / 'scripts'):
            hashes[str(path.relative_to(root))] = sha(path)
    (root / 'sha256-manifest.json').write_text(json.dumps(
        {'version': 'semifinal-query-visualization-candidate-v1', 'files': hashes},
        ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'schema_validated': 12, 'report': str(root / 'REPORT.md'),
                      'manifest_files': len(hashes)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
