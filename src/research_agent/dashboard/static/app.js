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

async function api(path) {
  const resp = await fetch(path);
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
};
const EVENT_ZH = {
  "search-done": "检索完成", "paper-ingested": "文献入库", "paper-error": "入库失败",
  "enrich-done": "元数据回补完成", "assessed": "完成质量评估",
  "human-review": "转入人工审核", "extracted": "完成知识提取",
  "skipped-no-model": "跳过提取(无模型)",
};

async function loadAgents() {
  const data = await api("/api/agents");
  $("#agentBoard").innerHTML = data.agents.map((a) => {
    const evs = (a.events || []).slice(0, 6);
    const rows = evs.map((e) => `
      <div class="mini">
        <span class="ev">${esc(EVENT_ZH[e.event] || e.event)} · ${esc(e.paper_key || "")}</span>
        <span>${fmtTs(e.ts)}</span>
      </div>`).join("") || '<div class="mini"><span class="ev">暂无事件</span></div>';
    return `
    <div class="agent" data-agent="${esc(a.id)}">
      <div class="agent-head">
        <span class="name">${esc(a.label)}</span>
        <span class="meta">事件 ${a.count} · 文献 ${a.paper_count} · ${fmtTs(a.last_ts)}</span>
      </div>
      <div class="agent-desc">${esc(a.desc)}</div>
      <div class="agent-events">${rows}</div>
    </div>`;
  }).join("");
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
    `节点 ${data.shown_nodes}/${data.total} · 边 ${data.shown_edges}` +
    (data.truncated ? "（已截断）" : "");
  if (state.network) { state.network.destroy(); state.network = null; }
  box.innerHTML = "";
  state.network = new vis.Network(box, { nodes, edges }, buildGraphOptions());
  state.network.on("click", (params) => {
    if (params.nodes && params.nodes.length) showNodeDetail(params.nodes[0]);
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
        <button class="dtab" data-t="quality">质量评估</button>
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
      qPane.innerHTML = '<p style="color:var(--muted)">尚未进行质量评估</p>';
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
  $("#view-graph").classList.toggle("hidden", tab !== "graph");
  $("#view-papers").classList.toggle("hidden", tab !== "papers");
}

/* ---------- 刷新 ---------- */
async function refreshAll() {
  try {
    await loadOverview();
    await Promise.all([loadAgents(), loadLogs()]);
    if (state.tab === "papers") await loadPapers();
    else await loadGraph(true);
  } catch (err) {
    toast("刷新失败: " + err.message);
  }
}

/* ---------- 事件绑定 ---------- */
function bind() {
  $("#refreshBtn").onclick = refreshAll;
  $("#tabGraph").onclick = () => { switchTab("graph"); loadGraph(true); };
  $("#tabPapers").onclick = () => { switchTab("papers"); loadPapers(); };
  $("#btnApplyGraph").onclick = () => { state.minConf = parseFloat($("#minConf").value); loadGraph(); };
  $("#btnResetGraph").onclick = () => {
    state.selectedTypes.clear();
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
  try {
    const health = await api("/api/health");
    $("#dbLabel").textContent = "数据库: " + health.db;
    await refreshAll();
  } catch (err) {
    toast("无法连接后端: " + err.message);
    $("#graphBox").innerHTML = `<p class="hint">无法连接后端，请确认服务已启动。</p>`;
  }
}

document.addEventListener("DOMContentLoaded", init);
