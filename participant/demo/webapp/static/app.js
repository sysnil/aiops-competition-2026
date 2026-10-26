/* global React, ReactDOM, echarts */
(function () {
  "use strict";
  const h = React.createElement;
  const evidenceModel = window.AIOpsEvidenceModel;
  const utcTime = value => { const d=new Date(value); return Number.isNaN(d.getTime())?"--":d.toISOString().slice(11,23); };
  const { useCallback, useEffect, useMemo, useRef, useState } = React;

  const defaultQuestion = "请分析 SF-001 的 60 分钟微服务系统监控数据，识别异常实体、异常类型、观测时间及证据，并输出结构化 JSON。";
  const phaseNames = { START:"初始化", DISCOVER:"发现", PLAN:"LLM 规划", ACT:"工具执行", OBSERVE:"数据观测", VERIFY:"证据验证", ANSWER:"生成答案", COMPLETE:"完成", SUBMIT:"提交评测", AUTH:"权限检查", STOP:"停止" };
  const typeNames = {
    "run.started":"运行启动", "process.started":"Agent 进程启动", "api.request":"Public API 查询", "tool.call":"工具调用",
    "llm.call":"LLM 规划/分析", "mcp.call":"MCP 诊断", "skill.lifecycle":"Skill 生命周期", "skill.summary":"Skill 执行汇总",
    "evidence.validation":"证据校验", "diagnosis.final":"最终诊断", "run.finished":"运行结束", "run.replay_started":"回放开始",
    "submission.request":"提交/评测请求", "topology.observed":"服务拓扑", "run.blocked":"运行阻断", "process.error":"进程异常", "run.stop_requested":"停止请求"
  };

  async function api(path, options) {
    const response = await fetch(path, { headers:{"Content-Type":"application/json"}, ...options });
    let data = {};
    try { data = await response.json(); } catch (_) {}
    if (!response.ok) {
      const detail = data.detail || data.data || data;
      throw new Error(detail.message || detail.code || `HTTP ${response.status}`);
    }
    return data;
  }
  function clip(value, max) {
    const text = typeof value === "string" ? value : JSON.stringify(value == null ? {} : value);
    return text.length > max ? text.slice(0, max) + "…" : text;
  }
  function formatTime(value) {
    if (!value) return "--:--:--";
    if (/^\d+(\.\d+)?$/.test(String(value))) return "--:--:--";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value).slice(11,19) : date.toLocaleTimeString("zh-CN", {hour12:false});
  }
  function statusClass(status) {
    const s = String(status || "").toLowerCase();
    if (s.includes("fail") || s.includes("error") || s.includes("blocked")) return "err";
    if (s.includes("ok") || s.includes("success") || s.includes("complete") || s === "200") return "ok";
    return "";
  }
  function gradeLabel(run) {
    if (!run) return "--";
    if (run.reference_status==="STALE_VERIFIER")return "旧参考待复核";
    if (run.score != null) {
      const isFinal = !!run.final_task_id || run.mode === "final";
      const score = isFinal ? Number((Number(run.score) * 10).toFixed(2)) : run.score;
      return `${score} / ${isFinal || run.task !== "qa" ? 100 : 10}${run.score_source==="APPROVED_RULE_REFERENCE"?"（参考）":""}`;
    }
    if (run.failure_code === "SUBMISSION_LIMIT_REACHED") return "预览次数已满";
    if (run.status === "FAILED" && !run.submission_id) return "未产生分数";
    if (run.submission_status === "PENDING_GT_APPROVAL") return "GT 待批准";
    if (["REJECTED","FAILED"].includes(run.submission_status)) return "评测未通过";
    if (run.submission_status === "COMPLETED") return "分数待发布";
    if (run.submission_id) return "评分中";
    return ["FAILED","STOPPED","INTERRUPTED"].includes(run.status) ? "未提交" : "等待提交";
  }
  function eventSummary(event) {
    const p = event.payload || {};
    if (event.type === "submission.request")return `${p.method||"POST"} ${p.path||""} · HTTP ${p.status} · 等待 ${p.delay_seconds}s 后重试（同一幂等键，第${p.attempt}次）`;
    if (event.type === "api.request") return `${p.method || "GET"} ${p.path || ""} · ${p.latency_ms ?? "?"}ms · HTTP ${p.status ?? "?"}`;
    if (event.type === "tool.call") return `${p.tool || p.event || "tool"} ${clip(p.arguments || p.result || {}, 110)}`;
    if (event.type === "llm.call") return `${p.model || "LLM"} · ${p.purpose || "analysis"} · ${p.latency_ms ?? "?"}ms`;
    if (event.type === "mcp.call") return `${p.tool || p.event || "MCP"} · ${clip(p.arguments || p.result || {}, 105)}`;
    if (event.type.startsWith("skill")) return clip(p, 120);
    if (event.type === "diagnosis.final") return p.answer&&p.answer.task_type==="SEMIFINAL_QUERY_VISUALIZATION"?`已生成 ${p.answer.question_id||"QA"} 图表与三问 answer.json`:`${(p.answer && p.answer.anomalies || []).length} 个异常，已生成 answer.json`;
    return clip(p.message || p.code || p.status || p, 120);
  }
  function extractRecord(evidence) { return evidence && typeof evidence.record === "object" ? evidence.record : evidence || {}; }
  function buildVisualsFromAnswer(answer) {
    const anomalies = answer && Array.isArray(answer.anomalies) ? answer.anomalies : [];
    const metrics = [], logs = [], traces = [], nodes = new Set(), edges = [];
    anomalies.forEach((a) => {
      if(String(a.entity_type).toLowerCase()==="service")nodes.add(a.entity_id || "unknown");
      (a.evidence || []).forEach((e) => {
        const r = extractRecord(e), modality = String(e.modality || e.evidence_type || "").toLowerCase();
        const item = { entity:a.entity_id, request_id:e.request_id, ...r };
        if (modality.includes("metric")) metrics.push(item);
        else if (modality.includes("log")) logs.push(item);
        else if (modality.includes("trace")) {
          traces.push(item);
          const source = r.service_name || r.service || a.entity_id;
          const target = r.peer_service || r.target_service || r.downstream_service;
          nodes.add(source); if (target) { nodes.add(target); edges.push({source, target}); }
        }
      });
    });
    return { anomalies, metrics, logs, traces, topology:{nodes:[...nodes].map((name)=>({id:name,name})),edges,provenance:"evidence-derived"} };
  }

  function Chart({ option, className="chart", style }) {
    const ref = useRef(null), chart = useRef(null);
    useEffect(() => {
      if (!ref.current || !window.echarts) return;
      chart.current = echarts.init(ref.current);
      const resize = () => chart.current && chart.current.resize();
      window.addEventListener("resize", resize);
      const observer=typeof ResizeObserver!=="undefined"?new ResizeObserver(resize):null;
      if(observer)observer.observe(ref.current);
      return () => { window.removeEventListener("resize", resize); if(observer)observer.disconnect(); chart.current && chart.current.dispose(); };
    }, []);
    useEffect(() => { chart.current && chart.current.setOption(option || {}, true); }, [option]);
    return h("div", {ref, className, style});
  }

  function Topbar({health, theme, setTheme,presentation,setPresentation}) {
    return h("header", {className:"topbar"},
      h("div", {className:"brand"}, h("div", {className:"brand-mark"}), h("div", null, h("strong", null, "AIOPS ARENA"), h("small", null, "AGENT INVESTIGATION CONSOLE"))),
      h("div", {className:"top-actions"},
        h("button",{className:"presentation-toggle",onClick:()=>{localStorage.setItem("aiops-presentation",presentation?"0":"1");setPresentation(!presentation);}},presentation?"退出投屏":"投屏模式"),
        h("div", {className:"connection"}, h("i", {className:`dot ${health ? "ok":""}`}), health ? "可视化服务在线" : "正在连接"),
        h("button", {className:"icon-btn", title:"切换主题", onClick:()=>setTheme(theme === "dark" ? "light":"dark")}, theme === "dark" ? "☼":"☾"),
        h("button", {className:"icon-btn", title:"全屏投屏", onClick:()=>document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()}, "⛶")
      )
    );
  }

  function Controls({form,setForm,onStart,onStop,busy,currentRun,activeRuns,maxActiveRuns,histories,historyTotal,onLoadMore,onRefreshHistory,selected,onReplay,caseState,questionState,topologyState,finalTasksState,health}) {
    const set = (key, value) => setForm((old)=>({...old,[key]:value}));
    const [historyFilter,setHistoryFilter] = useState('all');
    const caseItems = caseState&&caseState.upstream_status===200&&caseState.data&&Array.isArray(caseState.data.items)?caseState.data.items:[];
    const allQuestions = questionState&&questionState.upstream_status===200&&questionState.data&&Array.isArray(questionState.data.items)?questionState.data.items:[];
    const questionItems = allQuestions.filter((item)=>item.case_id===form.case_id);
    const selectedQuestion = questionItems.find((item)=>item.question_id===form.question_id);
    const allFinalTasks = finalTasksState&&finalTasksState.upstream_status===200&&finalTasksState.data&&Array.isArray(finalTasksState.data.items)?finalTasksState.data.items:[];
    const finalTasks = allFinalTasks;
    const taskKey = form.stage==='semifinal' ? (form.task==='qa'?`${form.mode}:${form.question_id}`:`${form.mode}:${form.case_id}:${form.task}`) : `final:${form.final_task_id}`;
    const activeForTask = activeRuns.find(run=>run.mode==='final'?`final:${run.final_task_id}`===taskKey:run.task==='qa'?`${run.mode}:${run.question_id}`===taskKey:`${run.mode}:${run.case_id}:${run.task}`===taskKey);
    const shownHistory = historyFilter==='current' ? histories.filter(run=>form.stage==='semifinal'?run.case_id===form.case_id:run.final_task_id===form.final_task_id) : histories;
    const chooseQuestion = (questionId) => {
      const question = allQuestions.find((item)=>item.question_id===questionId);
      setForm((old)=>({...old,question_id:questionId,case_id:question&&question.case_id||old.case_id}));
    };
    return h("aside", {className:"panel control"},
      h("div", {className:"panel-head"}, h("h2", null, "运行控制"), h("span", null, "LIVE CONTROL")),
      h("div", {className:"control-body"},
        h("div", {className:"section-title"}, "任务来源"),
        h("div", {className:"segmented"},
          h("button", {className:form.stage!=="semifinal"?"active":"",onClick:()=>setForm(old=>{const item=finalTasks.find(task=>task.task_id===old.final_task_id)||finalTasks[0];const stage=item&&item.kind==="PK"?"final_pk":"final_qualifier";return {...old,stage,task:stage,mode:"final",final_task_id:item&&item.task_id||null,case_id:item&&item.case_id||old.case_id};})}, "比赛任务"),
          h("button", {className:form.stage==="semifinal"?"active":"",onClick:()=>setForm(old=>({...old,stage:"semifinal",task:"qa",mode:"practice"}))}, "练习 / 半决赛")
        ),
        form.stage !== "semifinal" ? h("div", {className:"notice"}, finalTasksState&&finalTasksState.data&&finalTasksState.data.internal_preview?"内部测试：运行与提交仅产生预览分数，不计入正式比赛。":"决赛任务和 PK 轮次受 51 服务端权限、UTC 时间窗与来源 IP 控制。") : null,
        form.stage === "semifinal" ? h("div", null,
          h("div", {className:"section-title"}, "任务类型"),
          h("div", {className:"segmented"},
            h("button", {className:form.task==="qa"?"active":"",onClick:()=>set("task","qa")}, "查询·图表·三问"),
            h("button", {className:form.task==="anomaly"?"active":"",onClick:()=>set("task","anomaly")}, "异常检测 Agent")
          )
        ) : null,
        h("div", {className:"section-title"}, "观测范围"),
        form.stage==="semifinal"?h("label", {className:"field"}, h("span", null, "比赛模式"), h("select", {value:form.mode,onChange:e=>set("mode",e.target.value)}, h("option", {value:"practice"}, "Practice 练习"), h("option", {value:"semifinal"}, "Semifinal 半决赛"))):null,
        form.stage==="semifinal"?h("label", {className:"field"}, h("span", null, `Case（当前权限可见 ${caseItems.length} 个）`),
          h("select", {value:caseItems.some(x=>x.case_id===form.case_id)?form.case_id:"",disabled:!caseItems.length,onChange:e=>set("case_id",e.target.value)},
            !caseItems.length?h("option",{value:""},"暂无可访问 Case"):null,
            caseItems.map(item=>h("option",{key:item.case_id,value:item.case_id},`${item.case_id} · ${(item.available_modalities||[]).join("/")||"公开数据"}`))
          )
        ):null,
        form.stage!=="semifinal"?h("label",{className:"field"},h("span",null,`决赛任务（当前身份可见 ${finalTasks.length} 道）`),
          h("select",{value:finalTasks.some(x=>x.task_id===form.final_task_id)?form.final_task_id:"",disabled:!finalTasks.length,onChange:e=>{const item=finalTasks.find(x=>x.task_id===e.target.value);const stage=item&&item.kind==="PK"?"final_pk":"final_qualifier";setForm(old=>({...old,stage,task:stage,final_task_id:e.target.value,case_id:item&&item.case_id||old.case_id}));}},
            !finalTasks.length?h("option",{value:""},"暂无可见决赛任务"):null,
            finalTasks.map(item=>h("option",{key:item.task_id,value:item.task_id},`${item.task_id} · ${item.title||item.case_id}`))
          )):null,
        form.task === "qa" && form.stage==="semifinal" ? h("label", {className:"field"}, h("span", null, `Question（当前 Case 可见 ${questionItems.length} 道）`),
          h("select", {value:questionItems.some(x=>x.question_id===form.question_id)?form.question_id:"",disabled:!questionItems.length,onChange:e=>chooseQuestion(e.target.value)},
            !questionItems.length?h("option",{value:""},"当前 Case 暂无可访问题目"):null,
            questionItems.map(item=>h("option",{key:item.question_id,value:item.question_id},`${item.question_id} · ${item.title||"未命名题目"}`))
          )
        ) : null,
        form.stage==="semifinal"&&form.task==="qa"&&selectedQuestion?h("div",{className:"question-context"},
          h("strong",null,selectedQuestion.title||selectedQuestion.question_id),
          selectedQuestion.description?h("p",null,selectedQuestion.description):null):null,
        form.task === "qa" && questionState && questionState.upstream_status !== 200 ? h("div", {className:"notice"}, `题目列表当前 HTTP ${questionState.upstream_status}；未发布模板不会下发到浏览器。`) : null,
        form.stage==="semifinal"&&caseState && caseState.upstream_status !== 200 ? h("div", {className:"notice"}, `组委会接口当前 HTTP ${caseState.upstream_status}；Web 不会绕过比赛场次权限。`) : null,
        topologyState&&topologyState.upstream_status===200?h("div",{className:"notice info"},`已读取 ${topologyState.data&&topologyState.data.nodes?topologyState.data.nodes.length:0} 个服务、${topologyState.data&&topologyState.data.edges?topologyState.data.edges.length:0} 条依赖边。`):null,
        h("div", null, h("div", {className:"section-title"}, "Agent 能力"),
        h("div", {className:"segmented"},
          h("button", {className:form.agent==="baseline"?"active":"",onClick:()=>set("agent","baseline")}, "Baseline"),
          h("button", {className:form.agent==="llm"?"active":"",disabled:!health||!health.llm_configured,title:health&&health.llm_configured?"使用已配置的模型":"本机尚未配置 LLM API",onClick:()=>set("agent","llm")}, "LLM Agent")
        ),
        form.task!=="qa"?h("div", {className:"switch-row",style:{marginTop:8}},
          h("button", {className:`switch ${form.skills?"on":""}`,onClick:()=>set("skills",!form.skills)}, h("span", null,"Skill"),h("i",{className:"toggle"})),
          h("button", {className:`switch ${form.mcp?"on":""}`,onClick:()=>set("mcp",!form.mcp)}, h("span", null,"MCP"),h("i",{className:"toggle"}))
        ):null),
        health&&!health.llm_configured?h("div",{className:"notice"},"本机尚未配置 LLM API；可用 Baseline 完成真实查询、绘图和提交。模型配置后可选择 LLM Agent。"):null,
        form.task === "anomaly" ? h("label", {className:"field"}, h("span",null,"诊断问题"), h("textarea", {value:form.question,onChange:e=>set("question",e.target.value)})) : form.task==="qa"?h("div",{className:"notice info"},"程序将按服务器发布的题目定义查询真实 Public 数据，生成 chart.png、chart_data.json 和三问 answer.json。"):h("div",{className:"notice info"},"任务描述、Case 和 UTC 窗口由 51 Final Task API 下发，浏览器不接受手工输入的正式题号。"),
        activeRuns.length?h("div",{className:"notice info"},`${activeRuns.length} 个任务正在运行。切换题目不会停止它们；每道题同一时刻只启动一次。`):null,
        h("div", {className:"run-actions"},
          h("button", {className:"primary",disabled:!!activeForTask||activeRuns.length>=maxActiveRuns||(form.stage!=="semifinal"&&!form.final_task_id)||(form.agent==="llm"&&(!health||!health.llm_configured)),onClick:onStart}, activeForTask?"本题执行中":activeRuns.length>=maxActiveRuns?"并发已满":form.agent==="llm"&&(!health||!health.llm_configured)?"模型未配置":form.stage!=="semifinal"||form.task==="qa"?"运行并自动提交":"启动真实 Agent"),
          h("button", {className:"stop",disabled:!busy,onClick:onStop,title:currentRun?`停止正在查看的 ${currentRun.final_task_id||currentRun.question_id||currentRun.case_id}`:"停止 Agent","aria-label":currentRun?`停止 ${currentRun.final_task_id||currentRun.question_id||currentRun.case_id}`:"停止 Agent"},"■")
        ),
        h("div", {className:"notice info"}, "Token 只由后端读取；浏览器仅接收脱敏后的工具调用与证据摘要。"),
        activeRuns.length?h("div",{className:"active-runs"},h("div",{className:"section-title"},"正在执行 · 点击切换查看"),activeRuns.map(run=>
          h("button",{key:run.run_id,className:`history-item ${selected===run.run_id?"active":""}`,onClick:()=>onReplay(run)},
            h("div",{className:"history-top"},h("span",null,`${run.final_task_id||run.question_id||run.case_id} · ${run.case_id}`),h("span",{className:"badge live"},run.status)),
            h("div",{className:"history-meta"},`${run.agent} · ${run.run_id}`)))):null,
        h("div", {className:"history"},
          h("div",{className:"history-heading"},h("div", {className:"section-title"}, `本机运行历史 · ${historyTotal} 条`),h("button",{className:"soft-btn history-refresh",title:"刷新 Web 与命令行历史","aria-label":"刷新历史",onClick:onRefreshHistory},"↻")),
          h("select",{value:historyFilter,onChange:e=>setHistoryFilter(e.target.value),"aria-label":"历史筛选"},
            h("option",{value:"all"},"全部运行"),h("option",{value:"current"},"仅当前题目")),
          h("div", {className:"history-list"}, shownHistory.map((run)=>
            h("button", {key:run.run_id,className:`history-item ${selected===run.run_id?"active":""}`,onClick:()=>onReplay(run)},
              h("div",{className:"history-top"},h("span",null,`${run.final_task_id||run.question_id||run.case_id} · ${run.case_id}`),h("span",{className:`badge ${run.delivery_mode==="live"?"live":"warn"}`},run.source==='web'?"Web":"命令行")),
              h("div",{className:"history-meta"},`${formatTime(run.started_at)} · ${run.agent} · ${run.status==="FAILED"?"运行失败":run.task==="qa"?`${run.answer_count} 个答案`:`${run.anomaly_count} 异常`}${run.score!=null?` · ${gradeLabel(run)}`:""} · ${run.failure_code||run.submission_status||run.run_id.slice(0,18)}`)
            )
          )),
          histories.length<historyTotal?h("button",{className:"soft-btn history-more",onClick:onLoadMore},`加载更早记录（${histories.length}/${historyTotal}）`):null
        )
      )
    );
  }

  function Kpis({currentRun,events,elapsed}) {
    const last = events[events.length-1], tokens = currentRun && currentRun.token_usage;
    const execution=currentRun&&currentRun.execution_elapsed_seconds!=null?currentRun.execution_elapsed_seconds:elapsed;
    const cards = [
      ["当前步骤", last ? (phaseNames[last.phase] || last.phase) : "等待启动", last ? (typeNames[last.type] || last.type) : "尚无执行事件", "#28d7e5"],
      ["执行耗时", execution == null ? "--" : `${execution.toFixed(1)} s`, currentRun&&currentRun.elapsed_seconds!=null?`Agent 分析 ${Number(currentRun.elapsed_seconds).toFixed(1)}s；PK以51开场/接收时间为准`:"本页从启动计时；PK以51开场/接收时间为准", "#4d8dff"],
      ["API 请求", String(currentRun && currentRun.api_requests || events.filter(e=>e.type==="api.request").length), "真实 Public API", "#40d99a"],
      ["LLM Tokens", tokens ? String(tokens.total_tokens ?? "未提供") : "未提供", tokens ? `输入 ${tokens.input_tokens??"?"} / 输出 ${tokens.output_tokens??"?"}`:"模型 API usage 未返回时不估算", "#9b7bff"]
    ];
    return h("div", {className:"kpis"}, cards.map((c)=>h("div",{className:"panel kpi",key:c[0],style:{"--accent":c[3]}},h("div",{className:"label"},c[0]),h("div",{className:"value",title:c[1]},c[1]),h("div",{className:"sub"},c[2]))));
  }

  function FinalTaskBoard({form,finalTasksState,currentRun,qualificationState,roundState,topologyState,visuals}) {
    const items=finalTasksState&&finalTasksState.data&&Array.isArray(finalTasksState.data.items)?finalTasksState.data.items:[];
    const archived=currentRun&&currentRun.public_task_snapshot;
    const selectedTask=items.find(item=>item.task_id===form.final_task_id);
    const showingArchived=archived&&archived.task_id===form.final_task_id&&(!selectedTask||selectedTask.case_id!==archived.case_id);
    const task=showingArchived?archived:selectedTask;
    const loading=!finalTasksState||finalTasksState.upstream_status===429||finalTasksState.upstream_status>=500||finalTasksState.upstream_status==="ERR";
    const ownRun=currentRun&&task&&currentRun.final_task_id===task.task_id&&currentRun.case_id===task.case_id?currentRun:null;
    const viewingOther=currentRun&&currentRun.final_task_id!==form.final_task_id;
    const qualification=qualificationState&&qualificationState.upstream_status===200?qualificationState.data:null;
    const round=roundState&&roundState.upstream_status===200?roundState.data:null;
    const counts=task&&task.public_record_counts||{};
    const topology=showingArchived?(visuals&&visuals.topology):topologyState&&topologyState.upstream_status===200&&topologyState.task_id===form.final_task_id?topologyState.data:null;
    const countText=value=>Number.isInteger(value)&&value>=0?value.toLocaleString("zh-CN"):"未提供";
    const start=task&&Date.parse(task.query_start_utc),end=task&&Date.parse(task.query_end_utc);
    const validWindow=Number.isFinite(start)&&Number.isFinite(end)&&end>start;
    const windowText=validWindow?`${new Date(start).toISOString().slice(0,16).replace("T"," ")}–${new Date(end).toISOString().slice(11,16)} UTC · ${Math.round((end-start)/60000)} 分钟`:"时间窗未提供";
    const facts=[
      ["Metrics 指标点",counts.metrics],["Logs 日志",counts.logs],
      ["Trace Spans",counts.traces],["Trace IDs",counts.trace_ids],
      ["公开拓扑服务",ownRun&&ownRun.public_topology_counts?ownRun.public_topology_counts.nodes:topology&&topology.nodes&&topology.nodes.length],["公开调用边",ownRun&&ownRun.public_topology_counts?ownRun.public_topology_counts.edges:topology&&topology.edges&&topology.edges.length]
    ];
    return h("section",{className:"panel final-board"},
      h("div",{className:"final-board-title"},
        h("div",null,h("strong",null,"当前比赛任务"),h("small",{className:"final-task-title"},task?`${task.task_id} · ${task.title||task.case_id}`:loading?"任务列表读取中 / 等待重试":"等待服务器发布任务")),
        h("span",{className:`badge ${task&&["OPEN","RUNNING"].includes(task.status)?"live":"warn"}`},task?(showingArchived?(ownRun&&["RUNNING","STARTING"].includes(ownRun.status)?"运行快照":"历史快照"):task.status):"未就绪")
      ),
      h("div",{className:"current-task-summary"},
        h("span",null,task?`${task.description||"任务信息由 51 比赛 API 下发。"} · Case ${task.case_id}`:loading?"任务接口正在读取或繁忙重试，不表示权限被取消。":"当前身份没有可访问的比赛任务。"),
        ownRun?h("div",{className:"final-total"},h("span",null,`${ownRun.status} · ${ownRun.submission_status||"尚未提交"}`),h("b",null,gradeLabel(ownRun))):null,
        viewingOther?h("strong",null,`当前正在查看 ${currentRun.final_task_id||currentRun.run_id} · ${currentRun.case_id}`):null
      ),
      task?h("div",{className:"task-data"},
        h("div",{className:"task-data-head"},h("strong",null,"公开观测数据"),h("span",null,windowText)),
        h("div",{className:"task-data-grid"},facts.map(([label,value])=>h("div",{className:"task-data-fact",key:label},h("span",null,label),h("strong",null,countText(value)))))
      ):null,
      task&&!showingArchived&&task.kind==="FT"&&qualification?h("div",{className:"notice info"},`${qualification.grading_mode==="PREVIEW"?"上半场三题平均（内部测试）":"上半场三题平均"}：${qualification.total_score??"待评"} / ${qualification.max_score??100} · 已评分 ${qualification.scored_count??0}/3（未提交按 0 分）`):null,
      task&&task.kind==="PK"&&round&&(!showingArchived||(round.task&&round.task.case_id===task.case_id)||(ownRun&&round.confirmed_result&&Object.values(round.confirmed_result.entries||{}).some(entry=>entry.submission_id===ownRun.submission_id)))?h("div",{className:"notice info"},round.winner_team_id?`服务端已确认胜者：${round.winner_team_id}${round.next_round_id?` · 下一轮 ${round.next_round_id}`:""}`:`PK 轮次：${round.status||"待定"}；胜者及同分决胜以 51 服务端结果为准。`):null
    );
  }

  function Timeline({events}) {
    const ref = useRef(null);
    useEffect(()=>{ if(ref.current) ref.current.scrollTop=ref.current.scrollHeight; },[events.length]);
    return h("div",{className:"timeline",ref}, events.length ? events.slice(-80).map((e,i)=>
      h("div",{className:"event",key:`${e.seq}-${i}`},
        h("div",{className:`event-icon ${statusClass(e.status)}`}, e.type.includes("llm")?"AI":e.type.includes("api")?"↗":e.type.includes("mcp")?"M":e.type.includes("skill")?"S":"•"),
        h("div",null,h("div",{className:"event-title"},typeNames[e.type]||e.type),h("div",{className:"event-summary",title:eventSummary(e)},eventSummary(e))),
        h("div",{className:"event-time"},formatTime(e.ts))
      )
    ) : h("div",{className:"empty"},h("div",null,h("b",null,"等待调查过程"),"启动 Agent 或选择历史运行进行回放")));
  }

  function topologyOption(visuals, theme) {
    const topology = visuals.topology || {nodes:[],edges:[]};
    const text = theme === "dark" ? "#cfe0ef":"#20374d";
    return {backgroundColor:"transparent",tooltip:{},animationDuration:700,series:[{type:"graph",layout:"force",roam:true,draggable:true,label:{show:true,color:text,fontSize:10},force:{repulsion:115,edgeLength:90},data:(topology.nodes||[]).map((n,i)=>({...n,symbolSize:i===0?42:31,itemStyle:{color:i===0?"#28d7e5":"#4d8dff",shadowBlur:18,shadowColor:"rgba(40,215,229,.22)"}})),links:topology.edges||[],lineStyle:{color:"#6887a6",width:1.2,curveness:.12},edgeSymbol:["none","arrow"]}]};
  }
  function metricOption(visuals, theme, family) {
    const series=evidenceModel.metricSeries(visuals.metrics||[],family),text=theme==="dark"?"#8ca3bb":"#60768c";
    return {animation:false,tooltip:{trigger:"axis",renderMode:"richText"},legend:{type:"scroll",top:4,textStyle:{color:text,fontSize:10}},grid:{left:64,right:24,top:42,bottom:44},xAxis:{type:"time",name:"UTC",axisLabel:{color:text,formatter:v=>utcTime(v).slice(0,8)}},yAxis:{type:"value",scale:true,axisLabel:{color:text},splitLine:{lineStyle:{color:"rgba(123,151,180,.1)"}}},series:series.map((g,i)=>({name:g.name,type:"line",smooth:false,showSymbol:true,symbolSize:5,data:g.data,lineStyle:{width:2},itemStyle:{color:["#28d7e5","#4d8dff","#9b7bff","#40d99a"][i%4]}}))};
  }
  function traceOption(group, theme, page=0) {
    const w=evidenceModel.waterfall(group),rows=w.rows.slice(page*60,(page+1)*60),text=theme==="dark"?"#8ca3bb":"#60768c";
    return {animation:false,tooltip:{trigger:"axis",renderMode:"richText",formatter:params=>{const p=params.find(p=>p.seriesName==="Span duration");if(!p)return "";const r=rows[p.dataIndex];return `${r.service||r.service_name||"service"} · ${r.operation_name||r.span_name||"span"}\nSpan: ${r.span_id}\nParent: ${r.parent_span_id||"无"}\n开始偏移: ${r.offset_ms.toFixed(3)} ms\n持续: ${r.duration_ms.toFixed(3)} ms\n${r.start_time||r.timestamp}`;}},grid:{left:185,right:26,top:22,bottom:36},xAxis:{type:"value",name:"相对时间 ms",min:0,max:Math.max(w.end_ms*1.03,1),axisLabel:{color:text},splitLine:{lineStyle:{color:"rgba(123,151,180,.1)"}}},yAxis:{type:"category",inverse:true,data:rows.map((r,i)=>`${"  ".repeat(Math.min(r.depth,8))}${r.service||r.service_name||"service"} · ${r.operation_name||r.span_name||r.span_id}`),axisLabel:{color:text,fontSize:10,overflow:"truncate",width:170}},series:[{name:"offset",type:"bar",stack:"trace",silent:true,data:rows.map(r=>r.offset_ms),itemStyle:{color:"transparent"},emphasis:{disabled:true}},{name:"Span duration",type:"bar",stack:"trace",data:rows.map(r=>({value:r.duration_ms,itemStyle:{color:String(r.status||"").toUpperCase()==="ERROR"?"#fa7186":"#9b7bff"}})),barMinHeight:1,barMaxWidth:16}]};
  }

  function TraceEvidence({visuals,theme,currentRun}) {
    const groups=useMemo(()=>evidenceModel.traceGroups(visuals.traces||[]).sort((a,b)=>b.spans.length-a.spans.length),[visuals.traces]);
    const [selected,setSelected]=useState(""),[detail,setDetail]=useState(null),[state,setState]=useState(""),[tracePage,setTracePage]=useState(0);
    const traceId=groups.some(g=>g.trace_id===selected)?selected:(groups[0]&&groups[0].trace_id)||"";
    useEffect(()=>{
      setDetail(null);setState("");setTracePage(0);
      if(!traceId||!currentRun)return;
      const controller=new AbortController();let cancelled=false;setState("正在读取完整 Trace 的公开记录…");
      fetch(`/api/runs/${encodeURIComponent(currentRun.run_id)}/trace/${encodeURIComponent(traceId)}`,{signal:controller.signal}).then(async response=>{
        const body=await response.json();if(cancelled)return;if(!response.ok)throw new Error((body.detail||{}).message||`HTTP ${response.status}`);
        setDetail(body);setState(body.truncated===true?"Public API 返回截断链路":body.truncated===false?"Public API 已返回本题归档内的全部 Span":"Public API 链路记录；完整性未声明");
      }).catch(e=>{if(!cancelled&&e.name!=="AbortError")setState(`完整链路暂不可用（${e.message}）；展示已归档样本`);});
      return()=>{cancelled=true;controller.abort();};
    },[traceId,currentRun&&currentRun.run_id]);
    if(!groups.length)return h("div",{className:"empty"},"尚无包含真实 Trace ID、Span ID 和时间的证据");
    const fetched=detail&&detail.trace_id===traceId?evidenceModel.traceGroups(detail.spans||[]).find(g=>g.trace_id===traceId):null;
    const group=fetched||groups.find(g=>g.trace_id===traceId),waterfall=evidenceModel.waterfall(group);
    const pages=Math.max(1,Math.ceil(waterfall.rows.length/60)),page=Math.min(tracePage,pages-1);
    return h("div",{className:"trace-evidence"},h("label",{className:"evidence-selector"},"Trace ID",h("select",{value:traceId,onChange:e=>setSelected(e.target.value)},groups.map(g=>h("option",{key:g.trace_id,value:g.trace_id},`${g.trace_id} · 页面样本 ${g.spans.length} spans`)))),
      h("p",{className:"evidence-note"},`${state||"已归档的部分链路"} · ${group.spans.length} spans${group.spans.length>60?`（分页查看全部，当前${page*60+1}–${Math.min((page+1)*60,group.spans.length)}）`:""}。横轴为相对首个已记录 Span 的毫秒偏移。`),
      waterfall.rows.some(r=>r.parent_missing)?h("p",{className:"evidence-note"},"部分父 Span 未包含在当前记录中，不推断缺失链路。"):null,
      group.spans.length>60?h("div",{className:"evidence-pagination"},h("button",{disabled:page===0,onClick:()=>setTracePage(page-1)},"上一组 Span"),h("span",null,`${page+1} / ${pages}`),h("button",{disabled:page>=pages-1,onClick:()=>setTracePage(page+1)},"下一组 Span")):null,
      h(Chart,{option:traceOption(group,theme,page),style:{height:Math.max(250,Math.min(700,waterfall.rows.length*23+65))+"px"}}));
  }

  function Investigation({events,visuals,theme}) {
    const timelineHead = h("div", {className:"panel-head"}, h("h3", null, "调查时间线"), h("span", null, `${events.length} EVENTS`));
    const topologyHead = h("div", {className:"panel-head"}, h("h3", null, "服务依赖拓扑"), h("span", null, "PUBLIC EVIDENCE DERIVED"));
    const topologyBody = (visuals.topology && visuals.topology.nodes || []).length
      ? h(Chart, {option:topologyOption(visuals, theme)})
      : h("div", {className:"empty"}, h("div", null, h("b", null, "等待 Trace / Topology 证据"), "仅展示来自 Public API 的真实依赖关系"));
    const left = h("section", null, timelineHead, h(Timeline, {events}));
    const right = h("section", null, topologyHead, topologyBody);
    return h("div", {className:"panel investigation"}, left, right);
  }

  function Evidence({tab,setTab,visuals,theme,currentRun}) {
    const [chosenMetric,setChosenMetric]=useState("");
    const metricNames=evidenceModel.metricFamilies(visuals.metrics||[]);
    const metric=metricNames.includes(chosenMetric)?chosenMetric:metricNames[0];
    const activeTab=visuals.qa?"qa":tab;
    const tabs=visuals.qa?[["qa","参赛图表与三问"]]:[["metrics","Metrics"],["logs","Logs"],["traces","Trace Waterfall"],["anomalies","异常与证据"],...((visuals.final_charts||[]).length?[["final_charts","异常图表"]]:[])];
    let body;
    if(activeTab==="qa"&&visuals.qa){
      const qa=visuals.qa, artifact=currentRun&&qa.chart_artifact?`/api/runs/${encodeURIComponent(currentRun.run_id)}/artifacts/${qa.chart_artifact}`:null;
      body=h("div",{className:"qa-result"},artifact?h("img",{src:artifact,alt:"参赛程序基于真实 Public 数据生成的图表",style:{width:"100%",maxHeight:"350px",objectFit:"contain"}}):null,
        h("p",null,`本题查询：${qa.source&&qa.source.modality||"Public 数据"} · ${qa.source&&qa.source.record_count||0} 条真实记录`),
        h("div",{className:"anomaly-view"},(qa.answers||[]).map((a,i)=>h("div",{className:"anomaly-card",key:i},h("h4",null,h("span",null,`Q${i+1}`),h("span",{className:"badge"},a.answer_type||"answer")),h("p",null,`${a.value}${a.unit?` ${a.unit}`:""}${a.timestamp?` @ ${a.timestamp}`:""}`)))));
    }
    if(activeTab==="metrics") body=metricNames.length?h("div",null,h("label",{className:"evidence-selector"},"指标",h("select",{value:metric,onChange:e=>setChosenMetric(e.target.value)},metricNames.map(name=>h("option",{key:name,value:name},name)))),h("p",{className:"evidence-note"},"本次查询或引用的真实样本，按实体分线；非完整监控曲线，缺失值不补零。时间为 UTC。"),h(Chart,{option:metricOption(visuals,theme,metric),className:"chart"})):h("div",{className:"empty"},"尚无指标证据");
    if(activeTab==="logs") body=h("div",{className:"log-view"},(visuals.logs||[]).length?(visuals.logs||[]).map((row,i)=>h("div",{className:"log-row",key:i},h("small",null,`${row.timestamp||"--"} · ${row.service_name||row.service||row.pod||row.entity||"UNATTRIBUTED"}`),h("code",null,row.message||row.body||clip(row,400)))):h("div",{className:"empty"},"尚无日志证据"));
    if(activeTab==="traces") body=h(TraceEvidence,{visuals,theme,currentRun});
    if(activeTab==="anomalies") body=h("div",{className:"anomaly-view"},(visuals.anomalies||[]).length?(visuals.anomalies||[]).map((a,i)=>h("div",{className:"anomaly-card",key:i},h("h4",null,h("span",null,a.entity_id),h("span",{className:"badge"},a.anomaly_type||a.suspected_fault_type||"UNKNOWN")),h("p",null,`${a.entity_type} · ${utcTime(a.start_time)} — ${utcTime(a.end_time)} UTC`),h("p",null,a.anomaly_description||`${(a.evidence||[]).length} 条可验证 Public 证据`))):h("div",{className:"empty"},"尚无异常结论"));
    if(activeTab==="final_charts") body=h("div",{className:"final-chart-gallery"},(visuals.final_charts||[]).map((chart,i)=>h("figure",{key:chart.filename||i},h("img",{src:currentRun?`/api/runs/${encodeURIComponent(currentRun.run_id)}/artifacts/${encodeURIComponent(chart.filename)}`:"",alt:chart.title||"异常图表"}),h("figcaption",null,chart.title||chart.filename))));
    if(currentRun&&currentRun.status==="FAILED"&&!visuals.qa&&!(visuals.metrics||[]).length&&!(visuals.logs||[]).length&&!(visuals.traces||[]).length&&!(visuals.anomalies||[]).length)body=h("div",{className:"empty"},currentRun.failure_code==="LLM_UNAVAILABLE"?"模型未配置，运行在生成答案前结束；没有可展示的观测证据。":"运行失败，未生成答案；没有可展示的观测证据。");
    return h("div",{className:"panel evidence-panel"},h("div",{className:"panel-head"},h("h3",null,visuals.qa?"本题查询结果":"多模态观测证据"),h("div",{className:"tabs"},tabs.map(t=>h("button",{key:t[0],className:activeTab===t[0]?"active":"",onClick:()=>setTab(t[0])},t[1])))),h("div",{className:"evidence-body"},body));
  }

  function Rightbar({currentRun,events,visuals,onReplay,replayProgress,busy}) {
    const anomalies=visuals.anomalies||[], first=anomalies[0], qa=visuals.qa, failed=currentRun&&currentRun.status==="FAILED", tools=events.filter(e=>["tool.call","api.request","mcp.call","skill.lifecycle","skill.summary","llm.call","submission.request"].includes(e.type)).slice(-80).reverse();
    const scoredTask=currentRun&&(currentRun.final_task_id||currentRun.task==="qa");
    const failureText=currentRun&&currentRun.failure_code==="SUBMISSION_LIMIT_REACHED"
      ? `Agent 已生成 ${anomalies.length} 个异常，但 51 拒绝了本次提交：预览次数已满。本次没有评测分数，原始结果已保存。`
      :currentRun&&currentRun.failure_code==="LLM_UNAVAILABLE"
        ?"本机模型不可用，本次没有生成可提交的答案。"
        :currentRun&&currentRun.answer_present?`已保存 ${anomalies.length} 个分析候选及证据，但${currentRun.final_answer_ready?"自动提交未完成":"决赛答案格式转换未完成"}；请查看失败原因，原记录保留。`:"运行在生成答案前失败；请查看运行日志。";
    return h("aside",{className:"rightbar"},
      h("section",{className:"panel result-hero"},h("div",{className:"result-kicker"},currentRun&&currentRun.score_source==="APPROVED_RULE_REFERENCE"?"组委会独立参考评测":scoredTask?"51 服务端评测":"诊断结果"),
        scoredTask?h("div",{className:`result-score ${currentRun.score!=null?"scored":failed?"unscored":""}`},
          h("span",null,currentRun.score_source==="APPROVED_RULE_REFERENCE"?"现行规则参考分":"本题分数"),h("strong",null,gradeLabel(currentRun)),
          h("small",null,currentRun.score!=null?`${currentRun.rubric_version||"51 当前评分规则"}${currentRun.internal_preview?" · 内部测试，不计正式比赛":" · 51 服务端评分"}`:currentRun.submission_id?"等待 51 返回当前版本评测结果":"尚无有效提交")):null,
        h("div",{className:"result-entity"},failed?(currentRun.answer_present?"分析已保存，流程未完成":"运行失败"):qa?(qa.question_id||"查询题"):first?first.entity_id:currentRun&&currentRun.answer_present?"已完成，未检出异常":"等待诊断结果"),
        h("span",{className:"result-cause"},failed?(currentRun.failure_code||"AGENT_FAILED"):qa?"图表 + 三问已生成":first?(first.anomaly_type||first.suspected_fault_type||"UNKNOWN"):currentRun&&currentRun.answer_present?"已生成答案，异常列表为空":"尚未生成 answer.json"),
        h("p",{className:"result-copy"},failed?failureText:qa?`已基于真实 Public API 生成 ${qa.series_count||0} 条曲线和 ${(qa.answers||[]).length} 个结构化答案。`:first?`Agent 已输出 ${anomalies.length} 个异常事件；全部结果见“异常与证据”。`:(currentRun&&currentRun.task==="qa"?"等待本题的图表和三问答案。":"结果区域只展示 Agent 的实际输出。")),
        currentRun&&currentRun.selection_mode==="PUBLIC_DETECTOR_FALLBACK_LLM_FAILED"?h("p",{className:"notice"},"模型调用失败；当前答案由已验证的 Public 证据检测器回退生成，不代表 LLM 推理结果。"):null,
        h("div",{className:"result-meta"},
          h("div",null,h("small",null,"CASE"),h("strong",null,currentRun&&currentRun.case_id||"--")),
          currentRun&&currentRun.final_task_id?h("div",null,h("small",null,"FINAL TASK"),h("strong",null,currentRun.final_task_id)):null,
          h("div",null,h("small",null,"提交状态"),h("strong",null,currentRun&&currentRun.submission_status|| (failed?"提交失败":"--"))),
          h("div",null,h("small",null,"证据校验"),h("strong",null,currentRun&&currentRun.evidence_valid===true?"PASS":currentRun&&currentRun.evidence_valid===false?"FAIL":"--"))
        )
      ),
      h("section",{className:"panel"},h("div",{className:"panel-head"},h("h3",null,"工具与观测调用"),h("span",null,`${tools.length} CALLS`)),
        h("div",{className:"tool-list"},tools.length?tools.map((e,i)=>h("div",{className:"tool",key:`${e.seq}-${i}`},h("div",{className:"tool-head"},h("span",{className:"tool-name"},typeNames[e.type]||e.type),h("span",{className:`tool-status ${statusClass(e.status)==="err"?"fail":""}`},String(e.status||"observed"))),h("p",null,eventSummary(e)))):h("div",{className:"empty"},"工具调用将在此逐步出现"))
      ),
      h("section",{className:"panel replay-box"},h("div",{className:"panel-head",style:{padding:0,border:0}},h("h3",null,"赛后回放"),h("span",{className:`badge ${currentRun&&currentRun.delivery_mode==="live"?"live":"warn"}`},currentRun&&currentRun.delivery_mode==="live"?"真实实时事件":"延迟回放")),
        h("div",{className:"progress"},h("i",{style:{"--progress":`${replayProgress}%`}})),
        h("div",{className:"replay-actions"},h("button",{className:"soft-btn",disabled:!currentRun||busy,onClick:()=>currentRun&&onReplay(currentRun)},"↻ 从头回放"),h("span",{className:"speed"},currentRun?`${events.length} events · ${currentRun.delivery_mode==="replay"?"4×":"1×"}`:"选择历史运行"))
      )
    );
  }

  function App() {
    const [theme,setTheme]=useState(localStorage.getItem("aiops-theme")||"dark");
    const [presentation,setPresentation]=useState(new URLSearchParams(location.search).get("present")==="1"||localStorage.getItem("aiops-presentation")==="1");
    const [health,setHealth]=useState(null), [caseState,setCaseState]=useState(null), [questionState,setQuestionState]=useState(null), [topologyState,setTopologyState]=useState(null), [finalTasksState,setFinalTasksState]=useState(null), [qualificationState,setQualificationState]=useState(null), [roundState,setRoundState]=useState(null), [histories,setHistories]=useState([]), [historyTotal,setHistoryTotal]=useState(0), [activeRuns,setActiveRuns]=useState([]), [maxActiveRuns,setMaxActiveRuns]=useState(4);
    const [form,setForm]=useState({case_id:"SF-001",mode:"final",agent:"baseline",skills:true,mcp:true,stage:"final_qualifier",task:"final_qualifier",question_id:"EX-P01",final_task_id:null,question:defaultQuestion,request_budget:120});
    const [currentRun,setCurrentRun]=useState(null), [events,setEvents]=useState([]), [visuals,setVisuals]=useState({anomalies:[],metrics:[],logs:[],traces:[],topology:{nodes:[],edges:[]}});
    const [busy,setBusy]=useState(false), [tab,setTab]=useState("metrics"), [error,setError]=useState(""), [elapsed,setElapsed]=useState(null), [replayProgress,setReplayProgress]=useState(0);
    useEffect(()=>{evidenceTabPinned.current=false;},[currentRun&&currentRun.run_id]);
    const eventSource=useRef(null), startedAt=useRef(null), lastSeq=useRef(0), activeIds=useRef(''), viewedRunId=useRef(null), evidenceTabPinned=useRef(false);
    useEffect(()=>{ document.documentElement.dataset.theme=theme;localStorage.setItem("aiops-theme",theme); },[theme]);
    const loadHistory=useCallback((offset=0)=>api(`/api/runs?source=all&limit=20&offset=${offset}`).then(x=>{setHistories(old=>offset?[...old,...(x.items||[]).filter(item=>!old.some(previous=>previous.run_id===item.run_id))]:(x.items||[]));setHistoryTotal(x.total||0);}).catch(e=>setError(e.message)),[]);
    const refreshHistory=useCallback(()=>loadHistory(0),[loadHistory]);
    const refreshActive=useCallback(()=>api('/api/runs/active').then(result=>{const rows=result.runs|| (result.run?[result.run]:[]);const ids=rows.map(run=>run.run_id).join(',');if(activeIds.current!==ids){activeIds.current=ids;refreshHistory();}setActiveRuns(rows);setMaxActiveRuns(result.max_active_runs||4);}).catch(()=>{}),[refreshHistory]);
    useEffect(()=>{
      const run=currentRun;
      if(!run||!run.final_task_id||!run.submission_id)return;
      let cancelled=false;
      const poll=()=>api(`/api/runs/${encodeURIComponent(run.run_id)}/grade`).then(data=>{
        if(cancelled||!data.run)return;
        setCurrentRun(old=>old&&old.run_id===run.run_id?{...data.run,delivery_mode:old.delivery_mode}:old);
        if(data.run.score!=null||data.run.submission_status!==run.submission_status)refreshHistory();
      }).catch(()=>{});
      poll();
      const timer=setInterval(poll,30000);
      return()=>{cancelled=true;clearInterval(timer);};
    },[currentRun&&currentRun.run_id,currentRun&&currentRun.submission_id,currentRun&&currentRun.submission_status,currentRun&&currentRun.score,currentRun&&currentRun.reference_status,refreshHistory]);
    useEffect(()=>{ api("/api/health").then(setHealth).catch(()=>setHealth(null));refreshHistory(); },[refreshHistory]);
    useEffect(()=>{refreshActive();const timer=setInterval(refreshActive,2000);return()=>clearInterval(timer);},[refreshActive]);
    useEffect(()=>{
      if(form.stage!=="semifinal")return;
      let cancelled=false;
      const controller=new AbortController();
      setCaseState(null);
      fetch(`/api/cases?mode=${form.mode}`,{signal:controller.signal}).then(async response=>{
        const data=await response.json();
        if(!cancelled)setCaseState(data && data.upstream_status ? data : {upstream_status:response.status,data});
      }).catch(e=>{if(!cancelled)setCaseState({upstream_status:"ERR",data:{message:e.message}});});
      return()=>{cancelled=true;controller.abort();};
    },[form.mode,form.stage]);
    useEffect(()=>{
      if(form.stage!=="semifinal")return;
      let cancelled=false;
      const controller=new AbortController();
      setQuestionState(null);
      fetch(`/api/questions?mode=${form.mode}`,{signal:controller.signal}).then(async response=>{
        const data=await response.json();
        if(!cancelled)setQuestionState(data&&data.upstream_status?data:{upstream_status:response.status,data});
      }).catch(e=>{if(!cancelled)setQuestionState({upstream_status:"ERR",data:{message:e.message}});});
      return()=>{cancelled=true;controller.abort();};
    },[form.mode,form.stage]);
    const caseItems=useMemo(()=>caseState&&caseState.upstream_status===200&&caseState.data&&Array.isArray(caseState.data.items)?caseState.data.items:[],[caseState]);
    const questionItems=useMemo(()=>questionState&&questionState.upstream_status===200&&questionState.data&&Array.isArray(questionState.data.items)?questionState.data.items:[],[questionState]);
    useEffect(()=>{
      if(!caseItems.length)return;
      if(!caseItems.some(item=>item.case_id===form.case_id))setForm(old=>({...old,case_id:caseItems[0].case_id}));
    },[caseItems,form.case_id]);
    useEffect(()=>{
      if(form.task!=="qa"||!questionItems.length)return;
      const available=questionItems.filter(item=>item.case_id===form.case_id);
      if(available.length&&!available.some(item=>item.question_id===form.question_id))setForm(old=>({...old,question_id:available[0].question_id}));
    },[questionItems,form.case_id,form.question_id,form.task]);
    useEffect(()=>{
      if(form.stage!=="semifinal")return;
      if(!form.case_id||!caseItems.some(item=>item.case_id===form.case_id)){setTopologyState(null);return;}
      let cancelled=false;
      setTopologyState({upstream_status:"LOADING",case_id:form.case_id,data:{nodes:[],edges:[]}});
      fetch(`/api/topology?mode=${encodeURIComponent(form.mode)}&case_id=${encodeURIComponent(form.case_id)}`).then(async response=>{
        const data=await response.json();
        if(!cancelled)setTopologyState(data&&data.upstream_status?data:{upstream_status:response.status,case_id:form.case_id,data});
      }).catch(e=>{if(!cancelled)setTopologyState({upstream_status:"ERR",case_id:form.case_id,data:{nodes:[],edges:[],message:e.message}});});
      return()=>{cancelled=true;};
    },[form.mode,form.case_id,form.stage,caseItems]);
    useEffect(()=>{
      let cancelled=false, timer=null;
      const refreshTasks=async()=>{
        try {
          const response=await fetch("/api/final/tasks"), data=await response.json();
          const state=data&&data.upstream_status?data:{upstream_status:response.status,data};
          if(cancelled)return;
          setFinalTasksState(previous=>state.upstream_status===429&&previous&&previous.upstream_status===200?previous:state);
          if(state.upstream_status===429||state.upstream_status>=500)timer=setTimeout(refreshTasks,30000);
        }catch(e){if(!cancelled){setFinalTasksState({upstream_status:"ERR",data:{message:e.message}});timer=setTimeout(refreshTasks,30000);}}
      };
      refreshTasks();
      fetch("/api/final/qualification").then(async response=>{const data=await response.json();setQualificationState(data&&data.upstream_status?data:{upstream_status:response.status,data});}).catch(()=>setQualificationState(null));
      return()=>{cancelled=true;if(timer)clearTimeout(timer);};
    },[]);
    useEffect(()=>{
      if(form.stage==="semifinal"||!finalTasksState||finalTasksState.upstream_status!==200)return;
      const items=finalTasksState.data.items||[];
      if(items.length&&!items.some(item=>item.task_id===form.final_task_id))setForm(old=>{const stage=items[0].kind==="PK"?"final_pk":"final_qualifier";return {...old,stage,task:stage,final_task_id:items[0].task_id,case_id:items[0].case_id,mode:"final"};});
    },[form.stage,form.final_task_id,finalTasksState]);
    useEffect(()=>{
      if(form.stage==="semifinal"||!form.final_task_id)return;
      let cancelled=false;
      setTopologyState({upstream_status:"LOADING",task_id:form.final_task_id,data:{nodes:[],edges:[]}});
      fetch(`/api/final/topology?task_id=${encodeURIComponent(form.final_task_id)}`).then(async response=>{const data=await response.json();if(!cancelled)setTopologyState(data&&data.upstream_status?data:{upstream_status:response.status,task_id:form.final_task_id,data});}).catch(e=>{if(!cancelled)setTopologyState({upstream_status:"ERR",task_id:form.final_task_id,data:{nodes:[],edges:[],message:e.message}});});
      return()=>{cancelled=true;};
    },[form.stage,form.final_task_id]);
    useEffect(()=>{
      if(form.stage!=="final_pk"||!form.final_task_id){setRoundState(null);return;}
      let cancelled=false,timer=null;
      const refresh=()=>fetch(`/api/final/round?round_id=${encodeURIComponent(form.final_task_id)}`).then(async response=>{
        const data=await response.json();if(cancelled)return;
        const state=data&&data.upstream_status?data:{upstream_status:response.status,data};
        setRoundState(state);
        if(state.data&&["RESULT_CONFIRMED","CLOSED"].includes(state.data.status)&&timer)clearInterval(timer);
      }).catch(()=>{});
      refresh();
      if(currentRun&&currentRun.final_task_id===form.final_task_id&&["COMPLETED","PREVIEW_COMPLETED"].includes(currentRun.submission_status))timer=setInterval(refresh,15000);
      return()=>{cancelled=true;if(timer)clearInterval(timer);};
    },[form.stage,form.final_task_id,currentRun&&currentRun.submission_status]);
    useEffect(()=>{ if(!busy||!startedAt.current)return; const timer=setInterval(()=>setElapsed((Date.now()-startedAt.current)/1000),100);return()=>clearInterval(timer); },[busy]);
    function closeStream(){ if(eventSource.current){eventSource.current.close();eventSource.current=null;} }
    function showRunTask(run) {
      if(["practice","semifinal"].includes(run.mode)&&run.case_id&&run.case_id!=="unknown")setForm(old=>({...old,stage:"semifinal",mode:run.mode,case_id:run.case_id,question_id:run.question_id||old.question_id,task:run.task==="qa"?"qa":old.task}));
      else if(run.mode==="final"&&run.final_task_id){const stage=String(run.final_task_id).startsWith("FT-")?"final_qualifier":"final_pk";setForm(old=>({...old,stage,task:stage,mode:"final",case_id:run.case_id,final_task_id:run.final_task_id}));}
    }
    function connect(run,replay,initialVisuals) {
      closeStream();lastSeq.current=0;viewedRunId.current=run.run_id;setEvents([]);setVisuals(initialVisuals||{anomalies:[],metrics:[],logs:[],traces:[],topology:{nodes:[],edges:[]}});setReplayProgress(0);setCurrentRun({...run,delivery_mode:replay?"replay":"live"});
      setBusy(!replay);startedAt.current=!replay&&run.started_at?Date.parse(run.started_at):null;
      setElapsed(replay?run.elapsed_seconds==null?null:Number(run.elapsed_seconds):run.started_at&&Number.isFinite(Date.parse(run.started_at))?Math.max(0,(Date.now()-Date.parse(run.started_at))/1000):null);
      localStorage.setItem("aiops-last-run",run.run_id);
      const replaySpeed=replay?4:1;
      const source=new EventSource(`/api/runs/${encodeURIComponent(run.run_id)}/events?replay=${replay?"true":"false"}&speed=${replaySpeed}`); eventSource.current=source;
      source.addEventListener("agent_event",(message)=>{if(eventSource.current!==source)return;const e=JSON.parse(message.data);const seq=Number(e.seq||0);if(seq&&seq<=lastSeq.current)return;if(seq)lastSeq.current=seq;setEvents(old=>{const next=[...old,e];setReplayProgress(Math.min(98,next.length*1.5));return next;});if(e.type==="topology.observed"&&e.payload){setVisuals(old=>old.topology&&old.topology.provenance==="public-topology-api"&&(old.topology.nodes||[]).length>(e.payload.nodes||[]).length?old:({...old,topology:e.payload}));}if(e.type==="diagnosis.final"&&e.payload){const a=e.payload.answer||{},v=e.payload.visuals;setVisuals(old=>{const next=v&&typeof v==="object"?v:buildVisualsFromAnswer(a);if(old.topology&&(!next.topology||(old.topology.provenance==="public-topology-api"&&(old.topology.nodes||[]).length>(next.topology.nodes||[]).length)))next.topology=old.topology;return next;});if(!evidenceTabPinned.current)setTab(v&&v.qa?"qa":"anomalies");}if(e.type==="run.finished"||e.type==="run.interrupted"){setBusy(false);startedAt.current=null;setReplayProgress(100);refreshHistory();refreshActive();api(`/api/runs/${run.run_id}`).then(d=>{if(viewedRunId.current===run.run_id){setCurrentRun(d.run);if(d.visuals)setVisuals(d.visuals);}});}});
      source.addEventListener("replay_complete",()=>{if(eventSource.current!==source)return;setReplayProgress(100);closeStream();});
      source.addEventListener("run_complete",()=>{if(eventSource.current!==source)return;setBusy(false);startedAt.current=null;setReplayProgress(100);closeStream();refreshHistory();refreshActive();api(`/api/runs/${run.run_id}`).then(d=>{if(viewedRunId.current===run.run_id){setCurrentRun(d.run);if(d.visuals)setVisuals(d.visuals);}});});
      source.onerror=()=>{ if(source.readyState===EventSource.CLOSED) closeStream(); };
    }
    async function start(){setError("");try{const run=await api("/api/runs",{method:"POST",body:JSON.stringify(form)});connect(run,false);refreshHistory();refreshActive();}catch(e){setError(e.message);}}
    async function stop(){if(!currentRun)return;try{await api(`/api/runs/${currentRun.run_id}/stop`,{method:"POST"});}catch(e){setError(e.message);}}
    async function replay(run){setError("");try{const detail=await api(`/api/runs/${run.run_id}`);showRunTask(detail.run);const active=["STARTING","RUNNING","STOPPING"].includes(detail.run.status);connect(detail.run,!active,detail.visuals||buildVisualsFromAnswer({}));}catch(e){setError(e.message);}}
    useEffect(()=>{let cancelled=false;api("/api/runs/active").then(async result=>{if(cancelled)return;const rows=result.runs||(result.run?[result.run]:[]);if(rows.length){const saved=localStorage.getItem("aiops-last-run");const selected=rows.find(run=>run.run_id===saved)||rows[rows.length-1];const detail=await api(`/api/runs/${encodeURIComponent(selected.run_id)}`);if(!cancelled){showRunTask(detail.run);connect(detail.run,false,detail.visuals);}}else{const saved=localStorage.getItem("aiops-last-run");let detail=null;if(saved){try{detail=await api(`/api/runs/${encodeURIComponent(saved)}`);}catch(_){}}if(!detail){const history=await api("/api/runs?source=all&limit=1");if(history.items&&history.items.length)detail=await api(`/api/runs/${encodeURIComponent(history.items[0].run_id)}`);}if(!cancelled&&detail){viewedRunId.current=detail.run.run_id;showRunTask(detail.run);setCurrentRun({...detail.run,delivery_mode:"replay"});setVisuals(detail.visuals||buildVisualsFromAnswer({}));setElapsed(detail.run.elapsed_seconds==null?null:Number(detail.run.elapsed_seconds));}}}).catch(()=>{});return()=>{cancelled=true;};},[]);
    useEffect(()=>()=>closeStream(),[]);
    const publicTopology=topologyState&&topologyState.upstream_status===200&&((form.stage==="semifinal"&&topologyState.case_id===form.case_id)||(form.stage!=="semifinal"&&topologyState.task_id===form.final_task_id))?topologyState.data:null;
    const effectiveVisuals=useMemo(()=>{const own=visuals.topology||{nodes:[],edges:[]};const selectedCase=form.stage==="semifinal"?form.case_id:((finalTasksState&&finalTasksState.data&&finalTasksState.data.items||[]).find(t=>t.task_id===form.final_task_id)||{}).case_id;const topology=evidenceModel.topologyForRun(own,publicTopology,selectedCase,currentRun&&currentRun.case_id);return {...visuals,topology};},[visuals,publicTopology,form.stage,form.case_id,form.final_task_id,finalTasksState,currentRun&&currentRun.case_id]);
    return h("div",{className:`app ${presentation?"presentation":""}`},h(Topbar,{health,theme,setTheme,presentation,setPresentation}),error?h("div",{className:"toast",onClick:()=>setError("")},error):null,
      h("main",{className:"layout"},h(Controls,{form,setForm,onStart:start,onStop:stop,busy,currentRun,activeRuns,maxActiveRuns,histories,historyTotal,onLoadMore:()=>loadHistory(histories.length),onRefreshHistory:refreshHistory,selected:currentRun&&currentRun.run_id,onReplay:replay,caseState,questionState,topologyState,finalTasksState,health}),
        h("section",{className:`workspace ${form.stage!=="semifinal"?"final-mode":""}`},form.stage!=="semifinal"?h(FinalTaskBoard,{form,finalTasksState,currentRun,qualificationState,roundState,topologyState,visuals:effectiveVisuals}):null,h(Kpis,{currentRun,events,elapsed}),h(Investigation,{events,visuals:effectiveVisuals,theme}),h(Evidence,{tab,setTab:value=>{evidenceTabPinned.current=true;setTab(value);},visuals:effectiveVisuals,theme,currentRun})),
        h(Rightbar,{currentRun,events,visuals:effectiveVisuals,onReplay:replay,replayProgress,busy})
      )
    );
  }
  ReactDOM.createRoot(document.getElementById("root")).render(h(App));
})();
