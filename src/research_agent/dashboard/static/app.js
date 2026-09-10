/* research-agent 看板前端逻辑 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const state = {
  tab: "graph",
  network: null,
  nodeTypes: [],
  selectedTypes: new Set(),
  minConf: 0,
  graphLoading: false,
  domain: "",
};

/* ---------- 工具 ---------- */
function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fmtTs(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleTimeString("zh-CN", { hour12: false });
}

function fmtFull(iso) {
  if (!iso) return "-";
  const d = new Date(iso);
  return isNaN(d) ? String(iso) : d.toLocaleString("zh-CN", { hour12: false });
}

function fmtBytes(n) {
  if (n == null) return "-";
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(2) + " MB";
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.add("hidden"), 5000);
}

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} ${path}`);
  return resp.json();
}

const PALETTE = ["#4f8cff", "#2ecc8f", "#f5a623", "#ff5a6e", "#9b6bff", "#2dd4bf",
                 "#f472b6", "#a3e635", "#60a5fa", "#fb923c", "#c084fc", "#34d399",
                 "#f87171", "#22d3ee", "#fbbf24", "#e879f9"];
function typeColor(type) {
  let h = 0;
  for (const ch of String(type)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

/* ---------- 决策/状态 ---------- */
const DECISION = {
  knowledge: { label: "直接→知识提取", cls: "b-knowledge" },
  flagged: { label: "标记→知识提取", cls: "b-flagged" },
  enrich: { label: "元数据回补", cls: "b-enrich" },
  human: { label: "人工审核", cls: "b-human" },
};

function badgeDecision(d) {
  const m = DECISION[d] || { label: d || "未评估", cls: "b-none" };
  return `<span class="badge ${m.cls}">${esc(m.label)}</span>`;
}

/* ---------- 概览 chips ---------- */
async function loadOverview() {
  const o = await api("/api/overview");
  const qd = o.quality_decisions || {};
  const ds = DECISION;
  const chips = [
    ["文献总数", o.papers, o.paper_status ? Object.entries(o.paper_status).map(
      ([k, v]) => `${k}=${v}`).join(" · ") : ""],
    ["直接→提取", qd.knowledge || 0, ""],
    ["标记→提取", qd.flagged || 0, ""],
    ["元数据回补", qd.enrich || 0, ""],
    ["人工审核", (qd.human || 0) + " · 待办 " + ((o.paper_status || {}).human_review || 0), ""],
    ["本体节点", o.ontology.nodes, o.ontology.types ? `类型 ${o.ontology.types}` : ""],
    ["本体关系", o.ontology.edges, ""],
    ["科研超边", o.ontology.hyperedges || 0, o.ontology.domains ? `域 ${o.ontology.domains} · 通道 ${o.ontology.channels}` : ""],
    ["活动事件", o.logs, o.last_activity ? "最近 " + fmtTs(o.last_activity) : ""],
  ];
  $("#statChips").innerHTML = chips.map(
    ([k, v, s]) => `<div class="chip"><div class="k">${esc(k)}</div>
      <div class="v">${esc(v)}</div>${s ? `<div class="sub">${esc(s)}</div>` : ""}</div>`
  ).join("");

  const types = Object.keys(o.ontology.node_types || {}).sort();
  state.nodeTypes = types;
  renderTypeFilters(types);
  return o;
}

function renderTypeFilters(types) {
  const box = $("#typeFilters");
  if (!types.length) { box.innerHTML = ""; return; }
  box.innerHTML = types.map((t) =>
    `<label data-type="${esc(t)}"><input type="checkbox" style="display:none">${esc(t)}</label>`
  ).join("");
  [...box.querySelectorAll("label")].forEach((lab) => {
    const type = lab.dataset.type;
    lab.classList.toggle("on", state.selectedTypes.has(type));
    lab.onclick = () => {
      if (state.selectedTypes.has(type)) state.selectedTypes.delete(type);
      else state.selectedTypes.add(type);
      lab.classList.toggle("on", state.selectedTypes.has(type));
    };
  });
}

/* ---------- 智能体状态 ---------- */
const AGENT_TAG = {
  retrieval: "tag-retrieval",
  quality: "tag-quality",
  knowledge: "tag-knowledge",
  human_review: "tag-human_review",
  study: "tag-study",
  planner: "tag-study",
  knowledge_consumer: "tag-study",
  content_builder: "tag-study",
  reviewer: "tag-study",
};
const EVENT_ZH = {
  "search-done": "检索完成", "paper-ingested": "文献入库", "paper-error": "入库失败",
  "enrich-done": "元数据回补完成", "assessed": "完成质量控制",
  "human-review": "转入人工审核", "extracted": "完成知识提取",
  "review-submitted": "提交人工审核结果",
  "skipped-no-model": "跳过提取(无模型)",
  "session-start": "研究流程开始", "session-end": "研究流程结束",
  "planner": "工作规划", "knowledge_consumer": "知识消费",
  "content_builder": "内容形成", "reviewer": "审核校对",
};

async function loadAgents() {
  const data = await api("/api/status/nodes");
  const nodes = data.nodes || [];
  const statusText = {
    idle: ["pending", "等待/无事件"],
    running: ["running", "运行中"],
    done: ["done", "已完成"],
    waiting: ["needs_collection", "等待人工"],
    needs_collection: ["needs_collection", "等待补集"],
    error: ["error", "错误"],
  };
  const groups = {};
  nodes.forEach((a) => {
    (groups[a.group] = groups[a.group] || []).push(a);
  });
  $("#agentBoard").innerHTML = Object.entries(groups).map(([groupName, items]) => `
    <h2>${esc(groupName)}节点状态</h2>
    ${items.map((a) => {
      const [cls, txt] = statusText[a.status] || statusText.idle;
      const evs = (a.events || []).slice(0, 4);
      const rows = evs.map((e) => `
        <div class="mini">
          <span class="ev">${esc(EVENT_ZH[e.event] || e.event)} · ${esc(e.paper_key || "")}</span>
          <span>${fmtTs(e.ts)}</span>
        </div>`).join("") || '<div class="mini"><span class="ev">暂无事件</span></div>';
      return `
      <div class="agent ${esc(cls)}" data-agent="${esc(a.id)}">
        <div class="agent-head">
          <span class="name">${esc(a.label)}</span>
          <span class="status-pill ${esc(cls)}">${esc(txt)}</span>
        </div>
        <div class="agent-meta">事件 ${a.count} · 文献 ${a.paper_count} · ${fmtTs(a.last_ts)}</div>
        <div class="agent-desc">${esc(a.desc)}</div>
        <div class="agent-events">${rows}</div>
      </div>`;
    }).join("")}
  `).join("") || '<p style="color:var(--muted)">暂无节点事件</p>';
}

async function loadLogs() {
  const logs = await api("/api/logs?limit=60");
  $("#activityList").innerHTML = logs.map((l) => {
    const cls = AGENT_TAG[l.node] || "tag-quality";
    return `<li>
      <span class="tag ${cls}">${esc(EVENT_ZH[l.event] || l.event)}</span>
      <span class="msg" title="${esc(l.paper_key || "")}">${esc(l.paper_key || "")}</span>
      <span class="ts">${fmtTs(l.ts)}</span>
    </li>`;
  }).join("") || '<li class="msg">暂无活动</li>';
}

async function loadEvents() {
  try {
    const node = $("#eventNodeFilter").value;
    const search = $("#eventSearch").value.trim();
    const params = new URLSearchParams({ limit: "500" });
    if (node) params.set("node", node);
    if (search) params.set("search", search);
    const logs = await api("/api/logs?" + params.toString());
    $("#eventCount").textContent = `共 ${logs.length} 条`;
    $("#eventList").innerHTML = logs.map((l) => {
      let detail = "";
      try {
        detail = JSON.stringify(l.details || "", null, 1);
      } catch (err) { detail = String(l.details || ""); }
      return `
      <div class="event-row" data-eid="${esc(l.id ?? "")}">
        <div class="ev-head">
          <span class="tag ${AGENT_TAG[l.node] || "tag-study"}">${esc(EVENT_ZH[l.event] || l.event)}</span>
          <span><b>${esc(l.node)}</b> / ${esc(l.event)}</span>
          <span class="paper-key">${esc(l.paper_key || "-")}</span>
          <span>${fmtFull(l.ts)}</span>
        </div>
        <pre class="event-detail hidden">${esc(detail)}</pre>
      </div>`;
    }).join("") || '<div class="summary-card">没有符合条件的日志</div>';
    [...document.querySelectorAll(".event-row .ev-head")].forEach((head) => {
      head.onclick = () => {
        const pre = head.parentElement.querySelector(".event-detail");
        pre.classList.toggle("hidden");
      };
    });
  } catch (err) {
    toast("加载事件日志失败: " + err.message);
  }
}

/* ---------- 研究流程监控 ---------- */
function flowStatusMeta(s) {
  const map = {
    pending: ["pending", "等待"],
    running: ["running", "运行中"],
    done: ["done", "已完成"],
    needs_collection: ["needs_collection", "等待补集"],
    error: ["error", "错误"],
  };
  const m = map[s] || map.pending;
  return m;
}

async function loadStudyFlow() {
  const data = await api("/api/study/status");
  const banner = $("#studyBanner");
  const overallCls = data.status === "running" ? "running" :
    (data.status === "idle" ? "pending" : "done");
  banner.innerHTML = `
    <span class="big"><i class="${data.status === "running" ? "pulse" : ""}"></i>
      ${esc(data.status === "running" ? "研究流程运行中" : data.status === "idle" ? "暂无运行记录" : "最近一次运行已完成")}
    </span>
    <span class="dim">请求：${esc(data.request || "-")}</span>
    <span class="dim">Run ${esc(data.run_id || "-")}</span>
    <span class="dim">开始 ${fmtFull(data.started_at)}</span>
    <span class="dim">结束 ${fmtFull(data.ended_at)}</span>`;
  const nodes = data.nodes || [];
  const nodeLabels = {
    planner: ["工作规划", "解析请求并生成任务单"],
    knowledge_consumer: ["知识消费", "读取本体模式/证据，必要时请求补集"],
    content_builder: ["内容形成", "依据证据卡生成可溯源草稿"],
    reviewer: ["审核校对", "核查引用与证据支持度"],
  };
  $("#studyNodes").innerHTML = nodes.length ? nodes.map((n) => {
    const [cls, txt] = flowStatusMeta(n.status);
    let detail = "";
    const d = n.details || {};
    if (n.id === "planner") detail = d.goal || (d.seed_terms || []).join("; ") || "";
    if (n.id === "knowledge_consumer") {
      detail = (d.patterns != null ? `模式 ${d.patterns} · 证据 ${d.evidence} · 覆盖率 ${d.coverage_score ?? "-"}` : d.reason) || "";
    }
    if (n.id === "content_builder") detail = d.title || (d.sections != null ? `${d.sections} 节` : "");
    if (n.id === "reviewer") detail = d.decision ? `决策 ${d.decision} · 问题 ${d.issues ?? 0}` : "";
    return `<div class="flow-node ${esc(cls)}">
      <div class="fn-name">${esc((nodeLabels[n.id] || [n.label])[0])}</div>
      <div class="fn-status"><span>${esc(txt)}${n.status === "running" ? '<span class="pulse"></span>' : ""}</span><span>${fmtTs(n.last_ts)}</span></div>
      <div class="fn-detail" title="${esc(detail)}">${esc(detail || n.desc || "")}</div>
    </div>`;
  }).join("") : '<div class="summary-card">尚无研究流程记录。运行 research-agent-study 后可在此查看。</div>';

  const summary = data.summary || {};
  const p = summary.plan || {};
  const k = summary.knowledge || {};
  const dr = summary.draft || {};
  const rv = summary.review || {};
  $("#studySummary").innerHTML = `
    <dl>
      <dt>目标</dt><dd>${esc(p.goal || "-")}</dd>
      <dt>领域</dt><dd>${esc(p.domain || "-")}</dd>
      <dt>任务类型</dt><dd>${esc(p.content_type || "-")}</dd>
      <dt>检索词</dt><dd>${esc((p.seed_terms || []).join("; ") || "-")}</dd>
      <dt>模式/证据</dt><dd>${k.patterns != null ? `${esc(k.patterns)} / ${esc(k.evidence)}` : "-"}</dd>
      <dt>覆盖率</dt><dd>${esc(k.coverage_score ?? "-")}</dd>
      <dt>内容标题</dt><dd>${esc(dr.title || "-")}</dd>
      <dt>章节数</dt><dd>${dr.sections != null ? esc(dr.sections) : "-"}</dd>
      <dt>审核决策</dt><dd>${esc(rv.decision || "-")} · 问题 ${esc(rv.issues ?? "-")}</dd>
      <dt>审核说明</dt><dd>${esc(rv.summary || "-")}</dd>
    </dl>`;

  const agentData = await api("/api/agents");
  $("#pipelineNodes").innerHTML = agentData.agents.map((a) => `
    <div class="pipeline-node">
      <span><b>${esc(a.label)}</b> · ${esc(a.desc)}</span>
      <span class="dim">事件 ${a.count} · 文献 ${a.paper_count} · ${fmtTs(a.last_ts)}</span>
    </div>`).join("") || '<p style="color:var(--muted)">暂无数据构建节点事件</p>';

  $("#studyEvents").innerHTML = (data.events || []).slice(-40).map((e) => {
    const det = e.details || {};
    let detail = "";
    if (e.event === "planner") detail = det.goal || "";
    if (e.event === "knowledge_consumer") {
      detail = det.patterns != null ? `patterns=${det.patterns}, evidence=${det.evidence}` : det.reason || "";
    }
    if (e.event === "content_builder") detail = det.title || "";
    if (e.event === "reviewer") detail = `${det.decision || ""} issues=${det.issues ?? 0}`;
    if (e.event === "session-start" || e.event === "session-end") detail = det.request || det.status || "";
    return `<div class="study-event">
      <span>${esc(EVENT_ZH[e.event] || e.event)}</span>
      <span>${esc(det.status || "")}</span>
      <span class="ev-detail" title="${esc(detail)}">${esc(detail)}</span>
      <span>${fmtFull(e.ts)}</span>
    </div>`;
  }).join("") || '<p style="color:var(--muted)">暂无本轮事件</p>';
}

/* ---------- 本体图谱 ---------- */
function buildGraphOptions() {
  return {
    autoResize: true,
    nodes: {
      shape: "dot",
      size: 16,
      borderWidth: 1,
      shadow: { enabled: true, size: 8 },
      font: { color: "#dbe4f5", size: 12, face: "Microsoft YaHei" },
      color: {
        border: "#0b1018",
        highlight: { border: "#fff", background: "#4f8cff" },
        hover: { border: "#fff", background: "#3b6fd4" },
      },
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.55 } },
      color: { color: "#4c5a78", highlight: "#4f8cff", hover: "#6f9eff" },
      font: { color: "#8aa0c0", size: 10, strokeWidth: 0 },
      smooth: { enabled: true, type: "dynamic" },
    },
    physics: { enabled: true, stabilization: { iterations: 200 } },
    interaction: { hover: true, tooltipDelay: 120 },
  };
}

function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

async function loadGraph(keepView = false) {
  if (state.graphLoading) return;
  state.graphLoading = true;
  try {
    const types = [...state.selectedTypes];
    const params = new URLSearchParams();
    if (types.length) params.set("types", types.join(","));
    if (state.minConf > 0) params.set("min_confidence", state.minConf);
    const q = $("#qSearch").value.trim();
    if (q) params.set("q", q);
    if (state.domain) params.set("domain", state.domain);
    const data = await api("/api/ontology?" + params.toString());
    drawGraph(data, keepView);
  } catch (err) {
    toast("加载本体图谱失败: " + err.message);
  } finally {
    state.graphLoading = false;
  }
}

function drawGraph(data, keepView) {
  const box = $("#graphBox");
  if (!data.nodes.length) {
    box.innerHTML = '<p class="hint">暂无本体节点 —— 先运行流水线入库论文。</p>';
    state.network = null;
    $("#graphStats").textContent = "";
    renderStructure(data);
    return;
  }
  const nodes = new vis.DataSet(data.nodes.map((n) => ({
    id: n.id,
    label: trunc(n.label, 24),
    title: `${esc(n.type)}<br>${esc(n.label)}<br>置信度 ${n.confidence}`,
    value: 6 + Math.round(n.confidence * 22),
    color: { background: typeColor(n.type) },
    type: n.type,
  })));
  const edges = new vis.DataSet(data.edges.map((e) => ({
    id: e.id, from: e.from, to: e.to,
    label: e.type,
    title: `${esc(e.type)}<br>置信度 ${e.confidence}`,
    width: 0.6 + e.confidence * 2.2,
  })));
  $("#graphStats").textContent =
    `节点 ${data.shown_nodes}/${data.total} · 边 ${data.shown_edges} · 超边 ${data.shown_hyperedges || 0} · 域 ${data.shown_domains || 0} · 通道 ${data.shown_channels || 0}` +
    (data.truncated ? "（已截断）" : "");
  renderStructure(data);
  if (state.network) { state.network.destroy(); state.network = null; }
  box.innerHTML = "";
  state.network = new vis.Network(box, { nodes, edges }, buildGraphOptions());
  state.network.on("click", (params) => {
    if (params.nodes && params.nodes.length) showNodeDetail(params.nodes[0]);
  });
}

function renderStructure(data) {
  const panel = $("#structurePanel");
  if (!panel) return;
  const domains = (data.domains || []).slice(0, 8);
  const channels = (data.channels || []).slice(0, 8);
  const hyperedges = (data.hyperedges || []).slice(0, 6);
  const roleText = (profile) => {
    if (!profile || typeof profile !== "object") return "";
    return Object.entries(profile).map(([role, types]) =>
      `${role}: ${Array.isArray(types) ? types.join("/") : types}`).join(" · ");
  };
  panel.innerHTML = `
    <div class="struct-block">
      <h4>节点域</h4>
      <div class="struct-chips">${domains.map((d) => `
        <button class="struct-chip domain-chip" data-domain-key="${esc(d.domain_key || "")}">
          <b>${esc(d.label || d.domain_key)}</b><span>${esc(d.member_count || 0)} 节点</span>
        </button>`).join("") || '<span class="muted">暂无域</span>'}</div>
    </div>
    <div class="struct-block">
      <h4>关系通道</h4>
      <div class="struct-list">${channels.map((c) => `
        <div class="struct-row"><b>${esc(c.relation_family || c.channel_key)}</b>
          <span>${esc(roleText(c.role_profile))}</span>
          <span>支持 ${esc(c.support_count || 0)} · 论文 ${esc(c.paper_count || 0)} · conf ${esc(c.confidence || 0)}</span>
        </div>`).join("") || '<span class="muted">暂无通道</span>'}</div>
    </div>
    <div class="struct-block">
      <h4>科研超边</h4>
      <div class="struct-list">${hyperedges.map((h) => {
        const members = (h.members || []).map((m) => `${m.role || "participant"}: ${m.name}`).join("; ");
        return `<div class="struct-row"><b>H-${String(h.hyperedge_id).padStart(4, "0")} ${esc(h.label || h.hyperedge_type)}</b>
          <span>${esc(members)}</span><span>conf ${esc(h.confidence)} · evidence ${esc((h.evidence || []).length)}</span></div>`;
      }).join("") || '<span class="muted">暂无超边；后续抽取会自动写入</span>'}</div>
    </div>`;
  [...panel.querySelectorAll(".domain-chip")].forEach((btn) => {
    btn.onclick = () => {
      state.domain = btn.dataset.domainKey || "";
      state.selectedTypes.clear();
      renderTypeFilters(state.nodeTypes);
      loadGraph();
    };
  });
}

/* ---------- 节点详情 ---------- */
async function showNodeDetail(id) {
  try {
    const data = await api(`/api/ontology/nodes/${id}`);
    if (!data) { toast("节点不存在"); return; }
    const n = data.node;
    const attrs = Object.entries(n.attributes || {});
    const alias = (n.aliases || []);
    const prov = (n.provenance || []);
    openDetail(`节点 #${n.node_id} · ${n.node_type}`);
    const body = $("#detailBody");
    body.innerHTML = `
      <div class="grid2">
        <div>
          <dl class="kv">
            <dt>名称</dt><dd><b>${esc(n.name)}</b></dd>
            <dt>类型</dt><dd><span class="chip-mini" style="color:${typeColor(n.node_type)}">${esc(n.node_type)}</span></dd>
            <dt>置信度</dt><dd><div class="conf-bar"><i style="width:${Math.round((n.confidence || 0) * 100)}%"></i></div> ${n.confidence}</dd>
            <dt>首次/最近</dt><dd>${fmtFull(n.first_seen_at)} / ${fmtFull(n.last_seen_at)}</dd>
          </dl>
          ${alias.length ? `<div class="box"><h4>别名</h4><div class="chips">${alias.map(a => `<span class="chip-mini">${esc(a)}</span>`).join("")}</div></div>` : ""}
        </div>
        <div>
          <div class="box"><h4>属性 (${attrs.length})</h4>
            ${attrs.length ? `<dl class="kv">${attrs.slice(0, 20).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(typeof v === "string" ? v : JSON.stringify(v))}</dd>`).join("")}</dl>` : "无"}
          </div>
          <div class="box"><h4>来源证据 (${prov.length})</h4>
            ${prov.map(p => `<div class="mini"><span class="ev">${esc(p.paper || "")}</span></div><div style="font-size:12px;color:var(--muted)">${esc(p.evidence || "")}</div>`).join("") || "无"}
          </div>
        </div>
      </div>
      <div class="box"><h4>相邻关系 (${data.neighbors.length})</h4>
        ${data.neighbors.map(e => `
          <div class="neighbor-row" data-other="${e.other_id}">
            <span>${e.direction === "out" ? "→" : "←"} <b>${esc(e.relation_type)}</b>
              → <span style="color:${typeColor(e.other_type)}">${esc(e.other_name)}</span>
              <span class="chip-mini">${esc(e.other_type)}</span></span>
            <span style="color:var(--muted)">conf ${e.confidence}</span>
          </div>`).join("") || "孤立节点"}
      </div>`;
    [...body.querySelectorAll(".neighbor-row")].forEach((row) => {
      row.onclick = () => showNodeDetail(parseInt(row.dataset.other, 10));
    });
  } catch (err) {
    toast("加载节点失败: " + err.message);
  }
}

/* ---------- 文献列表 ---------- */
async function loadPapers() {
  try {
    const papers = await api("/api/papers");
    $("#paperTbody").innerHTML = papers.map((p) => `
      <tr data-key="${esc(p.paper_key)}">
        <td>${fmtTs(p.created_at)}</td>
        <td>${esc(p.source || "")}</td>
        <td class="paper-title" title="${esc(p.title || "")}">${esc(p.title || "")}</td>
        <td>${esc(p.venue || "")}</td>
        <td>${esc(p.pub_year ?? "")}</td>
        <td>${badgeDecision(p.decision)}</td>
        <td>${p.quality != null ? p.quality.toFixed(3) : "-"}</td>
        <td>${esc(p.status || "")}</td>
        <td>${(p.extracted || {}).entities ?? 0} / ${(p.extracted || {}).relations ?? 0} / ${(p.extracted || {}).events ?? 0}</td>
        <td><button class="btn small">查看</button></td>
      </tr>`).join("") ||
      '<tr><td colspan="10" style="text-align:center;color:var(--muted)">暂无文献 —— 运行流水线检索入库</td></tr>';
    [...$("#paperTbody").querySelectorAll("tr[data-key]")].forEach((tr) => {
      tr.onclick = () => showPaper(tr.dataset.key);
    });
  } catch (err) {
    toast("加载文献失败: " + err.message);
  }
}

async function loadReviews() {
  try {
    const showHistory = $("#reviewHistoryToggle").checked;
    const data = await api("/api/reviews?history=" + showHistory);
    $("#reviewCount").textContent = showHistory
      ? `历史 ${data.history.length} 条`
      : `待处理 ${data.pending.length} 条`;
    const presets = data.presets || {};
    if (showHistory) {
      $("#reviewList").innerHTML = `
      <div class="table-wrap">
        <table><thead><tr>
          <th>时间</th><th>论文 key</th><th>标题</th><th>动作</th>
          <th>决策</th><th>审核意见</th>
        </tr></thead><tbody>
        ${(data.history || []).map((h) => `
          <tr>
            <td>${fmtTs(h.reviewed_at)}</td>
            <td>${esc(h.paper_key || "")}</td>
            <td class="paper-title" title="${esc(h.title || "")}">${esc(h.title || "")}</td>
            <td>${esc((presets[h.action] || {}).label || h.action || "")}</td>
            <td>${esc(h.decision || "")}</td>
            <td>${esc(h.rationale || "")}</td>
          </tr>`).join("") || '<tr><td colspan="6" class="muted">暂无审核历史</td></tr>'}
        </tbody></table>
      </div>`;
      return;
    }
    const pending = data.pending || [];
    $("#reviewList").innerHTML = pending.map((p, idx) => {
      const name = `review_action_${idx}`;
      const presetEntries = Object.entries(presets);
      return `
      <div class="review-card" data-key="${esc(p.paper_key)}">
        <div class="review-head">
          <div>
            <h4>${esc(p.title || p.paper_key)}</h4>
            <div class="review-meta">${esc(p.paper_key)} · ${esc(p.source || "")} · ${esc(p.venue || "")} · ${esc(p.pub_year ?? "")}</div>
          </div>
          <span class="badge b-human">人工审核</span>
        </div>
        <div class="review-quality">
          ${p.quality != null ? `Q ${p.quality} · A ${p.authority} · T ${p.timeliness}` : "尚无质量评分"} · 原决策 ${esc(p.decision || "-")}
        </div>
        ${p.rationale ? `<div class="review-reason"><b>质量节点说明：</b>${esc(p.rationale)}</div>` : ""}
        <div class="review-preview"><b>正文/摘要预览：</b><br><pre class="event-detail">${esc(p.clean_preview || "（无文本）")}</pre></div>
        <div class="review-actions">
          ${presetEntries.map(([val, meta], i) => `
            <label class="review-choice">
              <input type="radio" name="${name}" value="${esc(val)}" ${i === 0 ? "checked" : ""}>
              ${esc(meta.label)}
            </label>`).join("")}
          <label class="review-choice">
            <input type="radio" name="${name}" value="custom">
            自定义结果
          </label>
        </div>
        <div class="review-custom-fields">
          <select class="custom-decision" title="自定义决策">
            <option value="knowledge">通过并进入知识提取</option>
            <option value="flagged">标记后进入知识提取</option>
            <option value="enrich">退回元数据补全</option>
            <option value="rejected">拒绝</option>
            <option value="human">保持人工审核</option>
          </select>
          <input class="custom-result" placeholder="自定义审核结果 JSON 或文本（可选）">
        </div>
        <textarea class="review-rationale" rows="2" placeholder="审核意见 / 说明，可留空"></textarea>
        <div class="review-submit-row">
          <button class="btn small review-submit">提交审核结果</button>
        </div>
      </div>`;
    }).join("") || '<div class="summary-card">当前没有待人工审核文献。</div>';

    [...document.querySelectorAll(".review-card")].forEach((card) => {
      card.querySelector(".review-submit").onclick = async () => {
        const key = card.dataset.key;
        const action = card.querySelector('input[type="radio"]:checked').value;
        const rationale = card.querySelector(".review-rationale").value.trim();
        const customText = card.querySelector(".custom-result").value.trim();
        const payload = {
          paper_key: key,
          action,
          rationale,
        };
        if (action === "custom") {
          payload.decision = card.querySelector(".custom-decision").value;
        }
        if (customText) {
          try { payload.custom_result = JSON.parse(customText); }
          catch (err) { payload.custom_result = customText; }
        }
        try {
          const res = await api("/api/reviews/submit", {
            method: "POST",
            body: JSON.stringify(payload),
          });
          if (!res.ok) throw new Error(res.error || "提交失败");
          toast(`已提交: ${res.paper_key} → ${res.decision}`);
          await loadReviews();
        } catch (err) {
          toast("提交审核失败: " + err.message);
        }
      };
    });
  } catch (err) {
    toast("加载人工审核失败: " + err.message);
  }
}

/* ---------- 文献详情 ---------- */
async function showPaper(key) {
  try {
    const data = await api("/api/papers/" + encodeURIComponent(key));
    if (!data) { toast("文献不存在"); return; }
    const p = data.paper || {};
    const q = data.quality;
    openDetail("文献详情 · " + p.paper_key);
    const body = $("#detailBody");
    body.innerHTML = `
      <div class="dtabs">
        <button class="dtab active" data-t="meta">概览</button>
        <button class="dtab" data-t="quality">质量控制</button>
        <button class="dtab" data-t="text">精校正文</button>
        <button class="dtab" data-t="logs">处理日志</button>
        <button class="dtab" data-t="run">本体提取</button>
      </div>
      <div data-pane="meta"></div>
      <div data-pane="quality" class="hidden"></div>
      <div data-pane="text" class="hidden"></div>
      <div data-pane="logs" class="hidden"></div>
      <div data-pane="run" class="hidden"></div>`;

    const metaPane = body.querySelector('[data-pane="meta"]');
    metaPane.innerHTML = `
      <dl class="kv">
        <dt>标题</dt><dd><b>${esc(p.title || "")}</b></dd>
        <dt>来源</dt><dd>${esc(p.source || "")} · ${esc(p.publication_status || "")}</dd>
        <dt>期刊/库</dt><dd>${esc(p.venue || "")} (${esc(p.venue_issn || "")})</dd>
        <dt>年份</dt><dd>${esc(p.pub_year ?? "")} · ${esc(p.pub_date || "")}</dd>
        <dt>DOI</dt><dd>${esc(p.doi || "-")}</dd>
        <dt>被引次数</dt><dd>${esc(p.citation_count ?? "-")} · 平均H指数 ${esc(p.avg_h_index ?? "-")}</dd>
        <dt>PDF</dt><dd>${p.db_has_blob ? "本地库已存 " + fmtBytes(p.pdf_size) : "未存 BLOB"}</dd>
        <dt>精校文本</dt><dd>${esc(p.clean_chars ?? 0)} 字符</dd>
        <dt>当前状态</dt><dd>${esc(p.status || "")}</dd>
      </dl>
      <div class="box"><h4>作者（${(p.authors || []).length}）</h4>
        ${(p.authors || []).map(a => `
          <div class="mini"><span class="ev">${esc(a.name || "")}${a.orcid ? ` · ORCID ${esc(a.orcid)}` : ""}</span>
          <span style="color:var(--muted)">${esc((a.affiliations || []).join("; ") || "单位未知")}</span></div>`).join("") || "无"}
      </div>`;

    const qPane = body.querySelector('[data-pane="quality"]');
    if (q) {
      const bar = (v) => `<div class="conf-bar"><i style="width:${Math.round((v || 0) * 100)}%"></i></div>`;
      qPane.innerHTML = `
        <div class="grid2">
          <div class="box"><h4>评分</h4>
            <dl class="kv">
              <dt>权威性 A</dt><dd>${q.authority} ${bar(q.authority)}</dd>
              <dt>时效性 T</dt><dd>${q.timeliness} ${bar(q.timeliness)}</dd>
              <dt>质量 Q</dt><dd><b>${q.quality}</b> ${bar(q.quality)}</dd>
              <dt>决策</dt><dd>${badgeDecision(q.decision)}</dd>
              <dt>人工复核标记</dt><dd>${q.needs_review ? "是" : "否"}</dd>
              <dt>评估时间</dt><dd>${fmtFull(q.assessed_at)}</dd>
            </dl>
          </div>
          <div class="box"><h4>构成</h4>
            <dl class="kv">
              <dt>期刊/分区</dt><dd>${esc(q.venue_factor ?? "-")}${q.venue_quartile ? ` · ${esc(q.venue_quartile)}` : ""}<br><span style="color:var(--muted);font-size:12px">${esc(q.venue_note || "")}</span></dd>
              <dt>H指数因子</dt><dd>${esc(q.h_factor ?? "-")}</dd>
              <dt>被引因子</dt><dd>${esc(q.citation_factor ?? "-")}</dd>
              <dt>学科速度</dt><dd>${esc(q.field_velocity || "-")} · 半衰期 ${esc(q.half_life ?? "-")} 年</dd>
              <dt>阈值</dt><dd>直接 ≥ ${q.threshold_direct} / 标记 ≥ ${q.threshold_flag}</dd>
            </dl>
            ${(q.meta_missing || []).length ? `<div class="box"><h4>缺失元数据</h4><div class="chips">${q.meta_missing.map(m => `<span class="chip-mini">${esc(m)}</span>`).join("")}</div></div>` : ""}
            <div class="box"><h4>评估说明</h4><p>${esc(q.rationale || "")}</p></div>
          </div>
        </div>`;
    } else {
      qPane.innerHTML = '<p style="color:var(--muted)">尚未进行质量控制</p>';
    }

    body.querySelector('[data-pane="text"]').innerHTML =
      `<div class="box"><h4>精校正文预览（前 ${esc((p.clean_preview || "").length)} 字符 / 共 ${esc(p.clean_chars || 0)}）</h4>
       <pre class="clean">${esc(p.clean_preview || "（无精校文本）")}</pre></div>`;

    const logPane = body.querySelector('[data-pane="logs"]');
    logPane.innerHTML = `<div class="box"><h4>该文献的处理日志（${data.logs.length}）</h4>
      ${data.logs.map(l => `
        <div class="mini"><span class="ev">${esc(EVENT_ZH[l.event] || l.event)} [${esc(l.node)}]</span><span>${fmtFull(l.ts)}</span></div>
        <div style="font-size:12px;color:var(--muted)">${esc(JSON.stringify(l.details || ""))}</div>`).join("") || "无"}</div>`;

    const runPane = body.querySelector('[data-pane="run"]');
    if (data.run) {
      const c = data.run.counts || {};
      const ex = c.extracted || c;
      runPane.innerHTML = `<div class="grid2">
        <div class="box"><h4>提取统计</h4><dl class="kv">
          <dt>实体</dt><dd>${ex.entities ?? 0}</dd>
          <dt>关系</dt><dd>${ex.relations ?? 0}</dd>
          <dt>事件</dt><dd>${ex.events ?? 0}</dd>
          <dt>新增节点/边</dt><dd>${ex.new_nodes ?? 0} / ${ex.new_edges ?? 0}</dd>
          <dt>本体版本</dt><dd>${data.run.version ?? "-"}</dd>
          <dt>提取时间</dt><dd>${fmtFull(data.run.ran_at)}</dd>
        </dl></div>
        <div class="box"><h4>新增类型</h4><div class="chips">${(data.run.new_types || []).map(t => `<span class="chip-mini">${esc(t)}</span>`).join("") || "无"}</div></div>
      </div>`;
    } else {
      runPane.innerHTML = '<p style="color:var(--muted)">该文献尚未进行知识提取</p>';
    }

    [...body.querySelectorAll(".dtab")].forEach((btn) => {
      btn.onclick = () => {
        body.querySelectorAll(".dtab").forEach((b) => b.classList.toggle("active", b === btn));
        body.querySelectorAll("[data-pane]").forEach((pane) =>
          pane.classList.toggle("hidden", pane.dataset.pane !== btn.dataset.t));
      };
    });
  } catch (err) {
    toast("加载文献详情失败: " + err.message);
  }
}

/* ---------- 详情面板 ---------- */
function openDetail(title) {
  $("#detailTitle").textContent = title;
  $("#detailPanel").classList.remove("hidden");
  $("#detailPanel").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- Tab ---------- */
function switchTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  $("#view-workflow").classList.toggle("hidden", tab !== "workflow");
  $("#view-graph").classList.toggle("hidden", tab !== "graph");
  $("#view-papers").classList.toggle("hidden", tab !== "papers");
  $("#view-reviews").classList.toggle("hidden", tab !== "reviews");
  $("#view-events").classList.toggle("hidden", tab !== "events");
  $("#view-planner").classList.toggle("hidden", tab !== "planner");
}

/* ---------- 刷新 ---------- */
async function refreshAll() {
  try {
    await loadOverview();
    await Promise.all([loadAgents(), loadLogs(), loadStudyFlow()]);
    if (state.tab === "papers") await loadPapers();
    else if (state.tab === "graph") await loadGraph(true);
    else if (state.tab === "reviews") await loadReviews();
    else if (state.tab === "events") await loadEvents();
  } catch (err) {
    toast("刷新失败: " + err.message);
  }
}

async function loadDatabaseList() {
  const list = await api("/api/databases");
  const health = await api("/api/health");
  const select = $("#dbSelect");
  if (!list.some((d) => d.path === health.db)) {
    list.unshift({
      path: health.db,
      name: PathName(health.db),
      papers: 0, nodes: 0, edges: 0, logs: 0,
    });
  }
  select.innerHTML = list.map((d) => `
    <option value="${esc(d.path)}" ${d.path === health.db ? "selected" : ""}>
      ${esc(d.name)} · P${d.papers}/N${d.nodes}/E${d.edges}
    </option>`).join("");
  select.onchange = async () => {
    try {
      await api("/api/db/select", {
        method: "POST",
        body: JSON.stringify({ path: select.value }),
      });
      $("#dbLabel").textContent = "数据库: " + select.value;
      await refreshAll();
      if (state.tab === "workflow") await loadStudyFlow();
      if (state.tab === "events") await loadEvents();
      toast("已切换到数据库: " + PathName(select.value));
    } catch (err) {
      toast("切换数据库失败: " + err.message);
    }
  };
}

function PathName(p) {
  const parts = String(p || "").split(/[\\/]/);
  return parts[parts.length - 1] || p;
}

async function sendToPlanner(executeStudy) {
  const requestText = $("#plannerRequest").value.trim();
  if (!requestText) { toast("请先输入规划指令"); return; }
  const btn = executeStudy ? $("#btnPlannerStudy") : $("#btnPlannerPlan");
  const original = btn.textContent;
  btn.disabled = true;
  try {
    const endpoint = executeStudy ? "/api/study/run" : "/api/planner/run";
    const res = await api(endpoint, {
      method: "POST",
      body: JSON.stringify({ request: requestText }),
    });
    const panel = $("#plannerResult");
    panel.classList.remove("hidden");
    if (!res.ok) {
      panel.innerHTML = `<div class="box error-box">${esc(res.error || "执行失败")}</div>`;
      return;
    }
    if (executeStudy) {
      panel.innerHTML = `<div class="box success-box">研究任务已后台提交，Job ${esc(res.job_id)}。切换到“研究流程”页查看状态。</div>`;
      await loadStudyFlow();
    } else {
      const p = res.plan || {};
      panel.innerHTML = `<div class="box">
        <h4>规划结果</h4>
        <dl class="kv">
          <dt>目标</dt><dd>${esc(p.goal || "")}</dd>
          <dt>领域</dt><dd>${esc(p.domain || "")}</dd>
          <dt>任务类型</dt><dd>${esc(p.content_type || "")} / ${esc(p.task_kind || "")}</dd>
          <dt>检索策略</dt><dd>${esc((p.retrieval || {}).strategy || "broad")} · 证据缺口 ${esc((p.retrieval || {}).evidence_gap_enabled ? "是" : "否")} · 单领域深挖 ${esc((p.retrieval || {}).deep_single_domain_enabled ? "是" : "否")}</dd>
          <dt>检索词</dt><dd>${esc(((p.mission || {}).seed_terms || []).join("; ") || "-")}</dd>
          <dt>最大结果</dt><dd>${esc((p.mission || {}).max_results ?? "-")}</dd>
        </dl>
        <pre class="event-detail">${esc(JSON.stringify(p, null, 2))}</pre>
      </div>`;
    }
  } catch (err) {
    toast("规划指令执行失败: " + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

/* ---------- 事件绑定 ---------- */
function bind() {
  $("#refreshBtn").onclick = refreshAll;
  $("#tabWorkflow").onclick = () => { switchTab("workflow"); loadStudyFlow(); };
  $("#tabGraph").onclick = () => { switchTab("graph"); loadGraph(true); };
  $("#tabPapers").onclick = () => { switchTab("papers"); loadPapers(); };
  $("#tabReviews").onclick = () => { switchTab("reviews"); loadReviews(); };
  $("#tabEvents").onclick = () => { switchTab("events"); loadEvents(); };
  $("#tabPlanner").onclick = () => { switchTab("planner"); };
  $("#btnEventFilter").onclick = loadEvents;
  $("#btnEventReset").onclick = () => {
    $("#eventNodeFilter").value = "";
    $("#eventSearch").value = "";
    loadEvents();
  };
  $("#eventSearch").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadEvents();
  });
  $("#reviewHistoryToggle").onchange = loadReviews;
  $("#btnPlannerPlan").onclick = () => sendToPlanner(false);
  $("#btnPlannerStudy").onclick = () => sendToPlanner(true);
  $("#btnApplyGraph").onclick = () => { state.minConf = parseFloat($("#minConf").value); loadGraph(); };
  $("#btnResetGraph").onclick = () => {
    state.selectedTypes.clear();
    state.domain = "";
    state.minConf = 0;
    $("#minConf").value = 0;
    $("#minConfVal").textContent = "0";
    $("#qSearch").value = "";
    renderTypeFilters(state.nodeTypes);
    loadGraph();
  };
  $("#minConf").oninput = () => { $("#minConfVal").textContent = $("#minConf").value; };
  $("#qSearch").addEventListener("keydown", (e) => { if (e.key === "Enter") loadGraph(); });
  $("#detailClose").onclick = () => $("#detailPanel").classList.add("hidden");
  setInterval(() => {
    if ($("#autoRefresh").checked && !document.hidden) refreshAll();
  }, 6000);
}

/* ---------- 启动 ---------- */
async function init() {
  bind();
  const initialTab = location.hash.replace("#", "");
  if (initialTab === "workflow") switchTab("workflow");
  else if (initialTab === "papers") switchTab("papers");
  else if (initialTab === "reviews") switchTab("reviews");
  else if (initialTab === "events") switchTab("events");
  else if (initialTab === "planner") switchTab("planner");
  try {
    const health = await api("/api/health");
    $("#dbLabel").textContent = "数据库: " + health.db;
    await loadDatabaseList();
    await refreshAll();
  } catch (err) {
    toast("无法连接后端: " + err.message);
    $("#graphBox").innerHTML = `<p class="hint">无法连接后端，请确认服务已启动。</p>`;
  }
}

document.addEventListener("DOMContentLoaded", init);
