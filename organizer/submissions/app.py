"""Committee-only, read-only view of submitted answers and grading evidence."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import streamlit as st

from auth import verify_password
from data import DATABASE, SEMIFINAL_PLAN, load_rows, load_semifinal_ranking, read_answer, scoring_evidence
from data import FINAL_QUALIFIER_PLAN, load_final_qualifier_ranking


AUDIT = Path(os.environ.get("SUBMISSION_REVIEW_AUDIT", "/var/lib/aiops-submission-review/view-audit.jsonl"))


st.set_page_config(page_title="Competition | Submission Evidence", page_icon="▦", layout="wide")
st.markdown("""<style>
.block-container {max-width: 1540px; padding-top: 1.2rem;}
h1 {font-size: 1.7rem !important; letter-spacing: 0;}
h2 {font-size: 1.2rem !important; letter-spacing: 0;}
[data-testid="stSidebar"] {background: #f3f7f8;}
[data-testid="stMetricValue"] {font-size: 1.45rem;}
</style>""", unsafe_allow_html=True)


def authenticated() -> bool:
    if st.session_state.get("submission_review_authenticated"):
        return True
    st.title("提交评测证据")
    st.caption("组委会专用 · 只读 · UTC")
    if st.session_state.get("submission_review_attempts", 0) >= 5:
        st.error("本次会话尝试次数过多，请重新连接。")
        return False
    with st.form("login"):
        reviewer = st.text_input("审核人", max_chars=80)
        password = st.text_input("组委会审核口令", type="password")
        submitted = st.form_submit_button("进入提交记录")
    if submitted:
        if len(reviewer.strip()) < 2 or not password:
            st.error("请输入审核人姓名和审核口令。")
            return False
        if verify_password(password):
            st.session_state.submission_review_authenticated = True
            st.session_state.submission_review_reviewer = reviewer.strip()
            st.session_state.submission_review_attempts = 0
            st.rerun()
        st.session_state.submission_review_attempts = st.session_state.get("submission_review_attempts", 0) + 1
        time.sleep(0.5)
        st.error("审核口令不正确。")
    return False


def audit_open(submission_id: str) -> None:
    event = {"at_utc": datetime.now(timezone.utc).isoformat(), "reviewer": st.session_state.submission_review_reviewer,
             "action": "OPEN_SUBMISSION_JSON", "submission_id": submission_id}
    fd = os.open(AUDIT, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as output:
        output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())


@st.cache_data(ttl=5, show_spinner=False)
def submissions() -> list[dict]:
    return load_rows()


@st.cache_data(ttl=5, show_spinner=False)
def semifinal_rankings() -> dict:
    return load_semifinal_ranking()


@st.cache_data(ttl=5, show_spinner=False)
def final_qualifier_rankings() -> dict:
    return load_final_qualifier_ranking()


@st.fragment(run_every=10)
def show_final_qualifier_rankings() -> None:
    if not st.session_state.get('submission_review_authenticated') or not FINAL_QUALIFIER_PLAN.exists():
        return
    with st.expander('决赛上半场正式计分 · 三题平均总分 / 排名 / 导出', expanded=True):
        try:
            result = final_qualifier_rankings()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            st.warning(f'决赛汇总暂不可用：{type(error).__name__}。请检查计分计划及只读快照新鲜度。')
            return
        plan = result['plan']
        zone = ZoneInfo(plan['timezone'])
        start = datetime.fromisoformat(plan['start_utc']).astimezone(zone).strftime('%Y-%m-%d %H:%M:%S')
        end = datetime.fromisoformat(plan['end_utc']).astimezone(zone).strftime('%H:%M:%S')
        st.caption(f'记分区间：{start}（含）至 {end}（不含），北京时间，以51收到提交的时间为准。页面每10秒刷新汇总。')
        st.caption('仅统计9支晋级队伍的OFFICIAL提交。每题满分100，取区间内最高分，三题平均为上半场总分；缺答在总分中贡献0，单题未出分仍标“待评分”。总分同分按更早取得该总分排序，分数与时间均相同则并列。')
        st.caption('题目仍持续开放；区间外提交保留其原始评测结果，但不计入本汇总。评分排队造成的延迟不影响按时收到的提交。组委会测试、PREVIEW和PK记录均排除。')
        if result['status'] == 'COMPLETE':
            st.success('计分区间已结束，区间内所有适用记录均已处理；当前为完整汇总。')
        elif result['status'] == 'NOT_STARTED':
            st.info('计分区间尚未开始。')
        else:
            st.warning('当前为暂定汇总：计分区间尚未结束，或仍有待评分/需核查的区间内提交。')
        a, b, c = st.columns(3)
        a.metric('决赛队伍', len(result['leaderboard']))
        b.metric('已计分题次', f"{result['selected_submission_count']} / 27")
        c.metric('待评分 / 需核查', len(result['pending_or_unresolved_submission_ids']))
        display, details = [], []
        for row in result['leaderboard']:
            item = {'排名': row['rank'], '队伍': row['team_id']}
            for task in plan['tasks']:
                value = row[task]
                item[task] = (f'{value:.2f}' + ('（待补评分）' if row[task + '_pending_count'] else '')) if value is not None else (
                    '待评分' if row[task + '_pending_count'] else '未提交' if row[task + '_status'] == 'NOT_SUBMITTED' else '无有效得分')
                received = row[task + '_received_at_utc']
                details.append({'队伍': row['team_id'], '题号': task, 'Case': plan['tasks'][task],
                                '百分制分数': value, '状态': row[task + '_status'],
                                '选中提交 ID': row[task + '_submission_id'],
                                '收件时间（北京时间）': datetime.fromisoformat(received).astimezone(zone).isoformat() if received else None})
            item.update({'上半场总分 / 100': row['total_score'], '已评分题数': row['scored_tasks'],
                         '待评分记录': row['pending_count'], '取得总分用时': row['total_score_elapsed_display']})
            display.append(item)
        st.dataframe(pd.DataFrame(display), hide_index=True, width='stretch', key='final_qualifier_leaderboard')
        with st.expander('逐题选中提交与收件时间'):
            st.dataframe(pd.DataFrame(details), hide_index=True, width='stretch')
        st.download_button('导出决赛上半场排名 CSV（含选中提交 ID）',
                           pd.DataFrame(result['leaderboard']).to_csv(index=False).encode('utf-8-sig'),
                           file_name='final-qualifier-20261023-ranking.csv', mime='text/csv', key='final_qualifier_csv')
        st.download_button('导出决赛上半场计分审计 JSON',
                           json.dumps(result, ensure_ascii=False, indent=2).encode(),
                           file_name='final-qualifier-20261023-audit.json', mime='application/json', key='final_qualifier_audit')
        st.caption(f"评分版本：{plan['required_rubric_version']}；汇总版本：{result['ranking_version']}；UTC生成时间：{result['generated_at_utc']}。")


def show_semifinal_rankings() -> None:
    if not SEMIFINAL_PLAN.exists():
        return
    with st.expander("半决赛正式计分 · 总分与排名 / CSV 导出", expanded=False):
        try:
            result = semifinal_rankings()
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            st.warning(f"半决赛汇总暂不可用：{type(error).__name__}。请检查计分计划和快照更新时间。")
            return
        plan = result["plan"]
        st.caption("Q01–Q10 每题 10 分，合计 100 分；每题取本场比赛窗口内最高分，同分取最早提交。总分相同，以从本场开场到首次取得该总分的用时排序；分数和用时均相同才并列。历史测试、预览及超时提交不计入。")
        roster = [{"场次": session, "日期": plan["date"], "开始（北京时间）": window["start"] or "待组委会通知",
                   "计分截止（北京时间）": window["end"], "队伍": ", ".join(window["teams"])}
                  for session, window in plan["windows"].items()]
        st.dataframe(pd.DataFrame(roster), hide_index=True, width="stretch")
        if any(w.get("schedule_status")=="AWAITING_ORGANIZER_START" for w in plan["windows"].values()):
            st.info("下半场等待组委会开放；开始时间及计分尚未启用。上半场成绩继续独立统计。")
        first_start = min(datetime.fromisoformat(plan["date"] + "T" + w["start"]).replace(tzinfo=ZoneInfo(plan["timezone"])) for w in plan["windows"].values() if w.get("start"))
        if datetime.now(timezone.utc) < first_start:
            st.info("比赛尚未开始，当前不生成正式排名。")
            return
        if result["status"] != "COMPLETE":
            st.warning("当前为临时汇总：比赛尚未全部结束，或仍有按时提交的记录待评分。")
        else:
            st.success("比赛窗口已结束，当前所有适用提交均已完成处理。")
        st.caption(f"待评分或需核查：{len(result['pending_or_unresolved_submission_ids'])} 条。缺答在总分中按 0 分计；未出分仍明确标为待处理。")
        st.caption("取得总分用时以51服务器收到提交的时间计算，按上／下半场各自开场时间归一化；评分排队和完成时间不影响快慢。0分队伍不通过提交0分答案获得速度优势。")
        columns = ["rank", "session_rank", "team_id", "session", "total_score", "total_score_elapsed_display", "maximum_score", "scored_questions"] + plan["questions"]
        frame = pd.DataFrame(result["leaderboard"])[columns].rename(columns={"rank": "总排名", "session_rank": "场内排名", "team_id": "队伍", "session": "场次", "total_score": "总分", "total_score_elapsed_display": "取得总分用时", "maximum_score": "满分", "scored_questions": "已评分题数"})
        st.dataframe(frame, hide_index=True, width="stretch")
        export = pd.DataFrame(result["leaderboard"]).to_csv(index=False).encode("utf-8-sig")
        st.download_button("导出半决赛总分排名 CSV（含逐题提交 ID）", export, file_name="semifinal-20261022-ranking.csv", mime="text/csv")
        st.download_button("导出计分审计 JSON", json.dumps({k: v for k, v in result.items() if k != "plan"}, ensure_ascii=False, indent=2).encode(), file_name="semifinal-20261022-audit.json", mime="application/json")


def main() -> None:
    if not authenticated():
        st.stop()
    st.title("提交评测证据")
    st.caption("全部记录来自 51 评分数据库；答案文件按提交 ID 读取并校验。UTC 时间，未出分不记为 0 分。决赛仅展示当前已批准 GT 与评分规则重新评测后的分数。")
    with st.sidebar:
        st.write(f"审核人：{st.session_state.submission_review_reviewer}")
        if st.button("退出审核", width="stretch"):
            st.session_state.submission_review_authenticated = False
            st.rerun()
        if st.button("刷新记录", width="stretch"):
            submissions.clear()
            semifinal_rankings.clear()
            final_qualifier_rankings.clear()
            st.rerun()

    show_final_qualifier_rankings()
    show_semifinal_rankings()

    try:
        if os.environ.get("SUBMISSION_REVIEW_SNAPSHOT") == "true":
            snapshot_time = DATABASE.stat().st_mtime
            age = max(0, time.time() - snapshot_time)
            timestamp = datetime.fromtimestamp(snapshot_time, timezone.utc).isoformat(timespec="seconds")
            st.caption(f"评分库一致性快照：{timestamp} · 每 5 秒更新；点击“刷新记录”获取最新记录。")
            if age > 30:
                st.warning(f"快照已 {int(age)} 秒未更新，以下可能不包含最新提交，请联系管理员检查快照服务。")
        rows = submissions()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        st.error(f"评分记录读取失败：{type(error).__name__}")
        st.stop()
    sources = sorted({row["source"] for row in rows})
    teams = sorted({row["team_id"] for row in rows})
    cases = sorted({row["case_id"] for row in rows})
    statuses = sorted({row["status"] for row in rows})
    grading_modes = sorted({row["grading_mode"] for row in rows})
    first, second, third, fourth = st.columns([1, 1.4, 1.3, 1.3])
    selected_sources = first.multiselect("赛段", sources)
    selected_teams = second.multiselect("队伍", teams)
    selected_cases = third.multiselect("Case", cases)
    selected_statuses = fourth.multiselect("评测状态", statuses)
    selected_modes = st.multiselect("评分模式", grading_modes)
    search = st.text_input("提交 ID / 题号筛选", placeholder="SUB-…、FSUB-…、FT-01、PK-R01")
    filtered = [row for row in rows
                if (not selected_sources or row["source"] in selected_sources)
                and (not selected_teams or row["team_id"] in selected_teams)
                and (not selected_cases or row["case_id"] in selected_cases)
                and (not selected_statuses or row["status"] in selected_statuses)
                and (not selected_modes or row["grading_mode"] in selected_modes)
                and (not search or search.lower() in (row["submission_id"] + " " + str(row["task_id"])).lower())]

    total, scored, pending, teams_count = st.columns(4)
    total.metric("提交记录", len(filtered))
    scored.metric("已有数值分数", sum(row["score"] is not None for row in filtered))
    pending.metric("尚无数值分数", sum(row["score"] is None for row in filtered))
    teams_count.metric("队伍数", len({row["team_id"] for row in filtered}))
    if not filtered:
        st.info("当前筛选没有提交记录。")
        return

    frame = pd.DataFrame([{
        "提交时间 UTC": row["received_at"], "赛段": row["source"], "队伍": row["team_id"],
        "Case": row["case_id"], "题号": row["task_id"], "状态": row["status"],
        "评分模式": row["grading_mode"], "展示分数": row["display_score"],
        "满分": row["display_max_score"], "评分版本": row.get("rubric_version"), "提交 ID": row["submission_id"],
    } for row in filtered])
    counts = frame.groupby(["赛段", "状态"], as_index=False).size().rename(columns={"size": "提交数"})
    chart = px.bar(counts, x="赛段", y="提交数", color="状态", barmode="group",
                   color_discrete_sequence=px.colors.qualitative.Safe)
    chart.update_layout(height=240, margin=dict(l=10, r=10, t=10, b=5), legend_title_text="")
    st.plotly_chart(chart, width="stretch")
    st.subheader("全部提交")
    selection = st.dataframe(frame, width="stretch", hide_index=True, height=430,
                             on_select="rerun", selection_mode="single-row", key="submission_table")
    indexes = selection.selection.rows
    chosen = filtered[indexes[0]] if indexes and indexes[0] < len(filtered) else None
    options = {f"{row['submission_id']} · {row['team_id']} · {row['task_id']}": row for row in filtered}
    selected_label = st.selectbox("按提交 ID 打开 JSON", ["请选择提交"] + list(options), key="submission_id_choice")
    if selected_label in options:
        chosen = options[selected_label]
    if chosen is None:
        st.caption("点击表格中的一行，或按提交 ID 选择记录，打开答案 JSON 和评分证据。")
        return

    st.subheader(f"{chosen['submission_id']} · {chosen['team_id']} · {chosen['task_id']}")
    try:
        opened = read_answer(chosen)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        st.error(f"提交文件无法验证：{type(error).__name__}。该记录保留在表格中，不能当作合格证据。")
        return
    if st.session_state.get("submission_review_last_opened") != chosen["submission_id"]:
        audit_open(chosen["submission_id"])
        st.session_state.submission_review_last_opened = chosen["submission_id"]
    if opened["verified"]:
        st.success("答案 SHA-256 与评分数据库登记值一致")
    else:
        st.error("答案内容与登记 SHA-256 不一致；请核查原始归档。")
    st.caption(f"文件：{opened['path']} · {len(opened['raw']):,} bytes · 文件 SHA-256 {opened['file_sha256']}")
    left, right = st.columns([1.35, 1])
    with left:
        st.markdown("#### 选手提交的原始 JSON")
        st.code(opened["raw"].decode("utf-8"), language="json", height=490)
        if opened["verified"]:
            st.download_button("下载该提交 JSON", opened["raw"],
                               file_name=f"{chosen['submission_id']}.json", mime="application/json")
    with right:
        st.markdown("#### 服务器评分记录")
        st.json(scoring_evidence(chosen), expanded=1)
        score_text = (f"{chosen['display_score']:g} / {chosen['display_max_score']}"
                      if chosen["display_score"] is not None else "未生成")
        st.caption(f"提交时间 {chosen['received_at']} · 状态 {chosen['status']} · 分数 {score_text}")


main()
