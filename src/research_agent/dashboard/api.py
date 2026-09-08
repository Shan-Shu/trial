"""看板后端数据访问：从 SQLite 读取本体/论文/质量/日志，供 REST API 使用。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from research_agent.config import Settings, settings as default_settings
from research_agent.db import connect


def _open(db_path: Path | str | None = None) -> sqlite3.Connection:
    return connect(Path(db_path) if db_path else default_settings.db_path)


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {r["name"] for r in rows}


def _table(conn: sqlite3.Connection, name: str) -> bool:
    return name in _tables(conn)


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not _table(conn, table):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _latest_run_per_paper(conn: sqlite3.Connection) -> dict[str, dict]:
    """paper_key → 该论文最近一次本体提取统计。"""
    if not _table(conn, "ontology_runs"):
        return {}
    rows = conn.execute(
        """
        SELECT r.paper_key, r.counts, r.new_types, r.ontology_version, r.ran_at
        FROM ontology_runs r
        WHERE r.run_id = (
            SELECT MAX(r2.run_id) FROM ontology_runs r2 WHERE r2.paper_key = r.paper_key
        )
        """
    ).fetchall()
    out = {}
    for r in rows:
        try:
            counts = json.loads(r["counts"] or "{}")
        except json.JSONDecodeError:
            counts = {}
        try:
            new_types = json.loads(r["new_types"] or "[]")
        except json.JSONDecodeError:
            new_types = []
        out[r["paper_key"]] = {
            "counts": counts, "new_types": new_types,
            "version": r["ontology_version"], "ran_at": r["ran_at"],
        }
    return out


def overview(db_path: Path | str | None = None) -> dict[str, Any]:
    conn = _open(db_path)
    try:
        statuses = {}
        if _table(conn, "papers"):
            for r in conn.execute("SELECT status, COUNT(*) AS c FROM papers GROUP BY status"):
                statuses[r["status"]] = r["c"]
        decisions = {}
        if _table(conn, "quality_results"):
            for r in conn.execute(
                "SELECT decision, COUNT(*) AS c FROM quality_results GROUP BY decision"
            ):
                decisions[r["decision"]] = r["c"]
        node_types = {}
        edge_types = {}
        if _table(conn, "ontology_nodes"):
            node_types = {
                r["node_type"]: r["c"] for r in conn.execute(
                    "SELECT node_type, COUNT(*) AS c FROM ontology_nodes GROUP BY node_type")
            }
        if _table(conn, "ontology_edges"):
            edge_types = {
                r["relation_type"]: r["c"] for r in conn.execute(
                    "SELECT relation_type, COUNT(*) AS c FROM ontology_edges GROUP BY relation_type")
            }
        last_activity = None
        if _table(conn, "processing_log"):
            r = conn.execute("SELECT MAX(ts) AS ts FROM processing_log").fetchone()
            last_activity = r["ts"]
        return {
            "papers": _count(conn, "papers"),
            "paper_status": statuses,
            "quality_decisions": decisions,
            "ontology": {
                "nodes": _count(conn, "ontology_nodes"),
                "edges": _count(conn, "ontology_edges"),
                "types": _count(conn, "ontology_type_registry"),
                "node_types": node_types,
                "edge_types": edge_types,
            },
            "logs": _count(conn, "processing_log"),
            "last_activity": last_activity,
        }
    finally:
        conn.close()


def list_papers(db_path: Path | str | None = None) -> list[dict[str, Any]]:
    conn = _open(db_path)
    try:
        runs = _latest_run_per_paper(conn)
        if not _table(conn, "papers"):
            return []
        rows = conn.execute(
            """
            SELECT p.paper_key, p.source, p.title, p.venue, p.pub_year, p.doi,
                   p.publication_status, p.citation_count, p.status, p.created_at,
                   p.authors_meta, q.quality, q.authority, q.timeliness,
                   q.decision AS q_decision, q.needs_review, q.meta_missing
            FROM papers p LEFT JOIN quality_results q ON q.paper_key = p.paper_key
            ORDER BY p.created_at DESC
            """
        ).fetchall()
        papers = []
        for r in rows:
            try:
                authors = json.loads(r["authors_meta"] or "[]")
            except json.JSONDecodeError:
                authors = []
            try:
                meta_missing = json.loads(r["meta_missing"] or "[]")
            except json.JSONDecodeError:
                meta_missing = []
            run = runs.get(r["paper_key"]) or {}
            counts = run.get("counts") or {}
            extracted = counts.get("extracted") or counts
            papers.append({
                "paper_key": r["paper_key"],
                "source": r["source"], "title": r["title"], "venue": r["venue"],
                "pub_year": r["pub_year"], "doi": r["doi"],
                "publication_status": r["publication_status"],
                "citation_count": r["citation_count"],
                "status": r["status"], "created_at": r["created_at"],
                "authors_count": len(authors),
                "affiliations_count": sum(
                    len(a.get("affiliations") or []) for a in authors),
                "quality": r["quality"], "authority": r["authority"],
                "timeliness": r["timeliness"], "decision": r["q_decision"],
                "needs_review": bool(r["needs_review"]),
                "meta_missing": meta_missing,
                "extracted": {
                    "entities": extracted.get("entities", 0),
                    "relations": extracted.get("relations", 0),
                    "events": extracted.get("events", 0),
                },
                "run_at": run.get("ran_at"),
            })
        return papers
    finally:
        conn.close()


def get_paper_detail(key: str, db_path: Path | str | None = None) -> dict[str, Any] | None:
    conn = _open(db_path)
    try:
        if not _table(conn, "papers"):
            return None
        row = conn.execute("SELECT * FROM papers WHERE paper_key=?", (key,)).fetchone()
        if not row:
            return None
        rec = dict(row)
        try:
            rec["authors"] = json.loads(rec.pop("authors_meta") or "[]")
        except json.JSONDecodeError:
            rec["authors"] = []
        blob = rec.pop("pdf_blob", None)
        rec["pdf_size"] = len(blob) if blob else rec.get("pdf_size")
        clean = rec.pop("clean_text", None) or ""
        rec["clean_preview"] = clean[:12000]
        rec["clean_chars"] = len(clean)

        quality = None
        if _table(conn, "quality_results"):
            q = conn.execute(
                "SELECT * FROM quality_results WHERE paper_key=?", (key,)
            ).fetchone()
            if q:
                quality = dict(q)
                try:
                    quality["meta_missing"] = json.loads(
                        quality.get("meta_missing") or "[]")
                except json.JSONDecodeError:
                    quality["meta_missing"] = []
        run = _latest_run_per_paper(conn).get(key)
        logs = []
        if _table(conn, "processing_log"):
            for r in conn.execute(
                "SELECT node, event, details, ts FROM processing_log "
                "WHERE paper_key=? ORDER BY id DESC LIMIT 100", (key,)
            ):
                d = dict(r)
                try:
                    d["details"] = json.loads(d.get("details") or "null")
                except json.JSONDecodeError:
                    d["details"] = None
                logs.append(d)
        return {
            "paper": rec, "quality": quality, "run": run, "logs": logs,
            "db_has_blob": blob is not None,
        }
    finally:
        conn.close()


def agent_status(db_path: Path | str | None = None,
                 recent: int = 40) -> list[dict[str, Any]]:
    """按节点聚合智能体工作状态与最近事件。"""
    conn = _open(db_path)
    try:
        agents = {
            "retrieval": {"id": "retrieval", "label": "文献检索节点",
                          "desc": "检索 / PDF 入库 / 清洗 / 元数据回补"},
            "quality": {"id": "quality", "label": "质量评估节点",
                        "desc": "A/T/Q 评分 / 路由 / 人工审核判定"},
            "knowledge": {"id": "knowledge", "label": "知识提取节点",
                          "desc": "预处理 / 实体关系事件抽取 / 动态本体写入"},
            "human_review": {"id": "human_review", "label": "人工审核",
                             "desc": "低质量或元数据无法补全的文献"},
        }
        for a in agents.values():
            a["events"] = []
            a["count"] = 0
            a["paper_count"] = 0
            a["last_ts"] = None
        recent_events: list[dict[str, Any]] = []
        if _table(conn, "processing_log"):
            rows = conn.execute(
                "SELECT paper_key, node, event, details, ts FROM processing_log "
                "ORDER BY id DESC LIMIT ?", (recent,)
            ).fetchall()
            for r in rows:
                d = dict(r)
                try:
                    d["details"] = json.loads(d.get("details") or "null")
                except json.JSONDecodeError:
                    d["details"] = None
                bucket = d["node"]
                if d["node"] == "quality" and d["event"] == "human-review":
                    bucket = "human_review"
                if bucket not in agents:
                    continue
                agents[bucket]["events"].append(d)
                recent_events.append({**d, "agent": bucket})
            # 聚合统计
            for r in conn.execute(
                "SELECT node, COUNT(*) AS c, COUNT(DISTINCT paper_key) AS pc, MAX(ts) AS mt "
                "FROM processing_log GROUP BY node"
            ):
                if r["node"] in agents:
                    agents[r["node"]]["count"] = r["c"]
                    agents[r["node"]]["paper_count"] = r["pc"]
                    agents[r["node"]]["last_ts"] = r["mt"]
            hrow = conn.execute(
                "SELECT COUNT(*) AS c, COUNT(DISTINCT paper_key) AS pc, MAX(ts) AS mt "
                "FROM processing_log WHERE node='quality' AND event='human-review'"
            ).fetchone()
            agents["human_review"]["count"] = hrow["c"]
            agents["human_review"]["paper_count"] = hrow["pc"]
            agents["human_review"]["last_ts"] = hrow["mt"]
        if _table(conn, "papers"):
            n_human = int(conn.execute(
                "SELECT COUNT(*) AS c FROM papers WHERE status='human_review'"
            ).fetchone()["c"])
            if n_human and agents["human_review"]["paper_count"] == 0:
                agents["human_review"]["paper_count"] = n_human
        return list(agents.values()), recent_events
    finally:
        conn.close()


def study_status(db_path: Path | str | None = None,
                 recent: int = 300) -> dict[str, Any]:
    """返回最近一次研究任务四节点的运行状态。"""
    conn = _open(db_path)
    try:
        if not _table(conn, "processing_log"):
            return {"run_id": None, "status": "idle", "request": None,
                    "nodes": [], "events": [], "summary": {}}
        rows = conn.execute(
            "SELECT id, paper_key, node, event, details, ts "
            "FROM processing_log WHERE node='study' "
            "ORDER BY id DESC LIMIT ?", (recent,)
        ).fetchall()
        parsed = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details") or "null")
            except json.JSONDecodeError:
                d["details"] = None
            parsed.append(d)
        start = next((d for d in parsed if d["event"] == "session-start"), None)
        if not start:
            return {"run_id": None, "status": "idle", "request": None,
                    "nodes": [], "events": [], "summary": {}}
        start_id = int(start["id"])
        run_events = [d for d in parsed if int(d["id"]) >= start_id]
        run_events.reverse()
        run_id = (start.get("details") or {}).get("run_id")

        defs = [
            ("planner", "工作规划节点",
             "解析用户请求，生成语料采集任务单"),
            ("knowledge_consumer", "知识消费节点",
             "从动态本体读取模式/证据，或请求补集"),
            ("content_builder", "内容形成节点",
             "基于模式卡/证据卡生成可溯源草稿"),
            ("reviewer", "审核校对节点",
             "核查引用、冲突与覆盖缺口"),
        ]
        nodes = []
        for event_name, label, desc in defs:
            evs = [d for d in run_events if d["event"] == event_name]
            status = "pending"
            last = evs[-1] if evs else None
            if last:
                det = last.get("details") or {}
                status = det.get("status", "done")
                if status in ("running",):
                    status = "running"
                elif status == "error":
                    status = "error"
                elif status == "needs_collection":
                    status = "needs_collection"
                elif status in ("reviewed", "manual_review", "done"):
                    status = "done"
            nodes.append({
                "id": event_name,
                "label": label,
                "desc": desc,
                "status": status,
                "last_ts": last["ts"] if last else None,
                "count": len(evs),
                "details": (last.get("details") or {}) if last else {},
            })
        end = next((d for d in run_events if d["event"] == "session-end"), None)
        overall = "running"
        if end:
            det = end.get("details") or {}
            overall = "completed" if det.get("status") != "error" else "error"
        summary = {}
        for ev in run_events:
            det = ev.get("details") or {}
            if ev["event"] == "planner" and det.get("status") == "done":
                summary["plan"] = {
                    "goal": det.get("goal"),
                    "domain": det.get("domain"),
                    "content_type": det.get("content_type"),
                    "task_kind": det.get("task_kind"),
                    "min_candidates": det.get("min_candidates"),
                    "seed_terms": det.get("seed_terms"),
                }
            elif ev["event"] == "knowledge_consumer":
                summary["knowledge"] = {
                    "patterns": det.get("patterns"),
                    "evidence": det.get("evidence"),
                    "coverage_score": det.get("coverage_score"),
                    "papers": det.get("papers"),
                }
            elif ev["event"] == "content_builder" and det.get("status") == "done":
                summary["draft"] = {
                    "title": det.get("title"),
                    "sections": det.get("sections"),
                    "markdown_chars": det.get("markdown_chars"),
                }
            elif ev["event"] == "reviewer":
                summary["review"] = {
                    "decision": det.get("decision"),
                    "issues": det.get("issues"),
                    "round": det.get("round"),
                    "summary": det.get("summary"),
                }
        return {
            "run_id": run_id,
            "status": overall,
            "request": (start.get("details") or {}).get("request"),
            "started_at": start["ts"],
            "ended_at": end["ts"] if end else None,
            "nodes": nodes,
            "events": run_events[-80:],
            "summary": summary,
        }
    finally:
        conn.close()


def activity_log(limit: int = 80, db_path: Path | str | None = None) -> list[dict[str, Any]]:
    conn = _open(db_path)
    try:
        if not _table(conn, "processing_log"):
            return []
        rows = conn.execute(
            "SELECT paper_key, node, event, details, ts FROM processing_log "
            "ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["details"] = json.loads(d.get("details") or "null")
            except json.JSONDecodeError:
                d["details"] = None
            out.append(d)
        return out
    finally:
        conn.close()


def ontology_graph(db_path: Path | str | None = None, *,
                   min_confidence: float = 0.0,
                   types: list[str] | None = None,
                   query: str | None = None,
                   limit: int = 800) -> dict[str, Any]:
    """返回本体子图（节点+边）。默认返回全部；支持按类型/置信度/名称过滤。"""
    conn = _open(db_path)
    try:
        if not _table(conn, "ontology_nodes"):
            return {"nodes": [], "edges": [], "truncated": False, "total": 0}
        sql = ("SELECT node_id, node_type, name, confidence, attributes, aliases, "
               "first_seen_at, last_seen_at FROM ontology_nodes WHERE 1=1")
        params: list[Any] = []
        if types:
            placeholders = ",".join("?" * len(types))
            sql += f" AND node_type IN ({placeholders})"
            params.extend(types)
        if min_confidence > 0:
            sql += " AND confidence >= ?"
            params.append(min_confidence)
        if query:
            sql += " AND (name LIKE ? OR normalized_name LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like])
        rows = conn.execute(sql, params).fetchall()
        total = len(rows)
        truncated = total > limit
        if truncated:
            rows = rows[:limit]
        node_ids: set[int] = set()
        nodes = []
        for r in rows:
            node_ids.add(int(r["node_id"]))
            nodes.append({
                "id": int(r["node_id"]),
                "label": r["name"],
                "type": r["node_type"],
                "confidence": round(float(r["confidence"] or 0), 3),
                "first_seen_at": r["first_seen_at"],
                "last_seen_at": r["last_seen_at"],
            })
        edges = []
        if node_ids and _table(conn, "ontology_edges"):
            ids = sorted(node_ids)
            if len(ids) <= limit * 2:
                ph = ",".join("?" * len(ids))
                erows = conn.execute(
                    f"SELECT edge_id, relation_type, source_node, target_node, confidence "
                    f"FROM ontology_edges WHERE source_node IN ({ph}) AND target_node IN ({ph})",
                    ids + ids,
                ).fetchall()
                for e in erows:
                    edges.append({
                        "id": int(e["edge_id"]),
                        "from": int(e["source_node"]),
                        "to": int(e["target_node"]),
                        "label": e["relation_type"],
                        "type": e["relation_type"],
                        "confidence": round(float(e["confidence"] or 0), 3),
                    })
        return {"nodes": nodes, "edges": edges,
                "truncated": truncated, "total": total,
                "shown_nodes": len(nodes), "shown_edges": len(edges)}
    finally:
        conn.close()


def ontology_node_detail(node_id: int,
                         db_path: Path | str | None = None) -> dict[str, Any] | None:
    conn = _open(db_path)
    try:
        if not _table(conn, "ontology_nodes"):
            return None
        r = conn.execute(
            "SELECT * FROM ontology_nodes WHERE node_id=?", (node_id,)
        ).fetchone()
        if not r:
            return None
        node = dict(r)
        for k in ("attributes", "aliases", "provenance"):
            try:
                node[k] = json.loads(node.get(k) or ("[]" if k == "aliases" else "{}"))
            except json.JSONDecodeError:
                node[k] = [] if k == "aliases" else {}
        neighbors = []
        if _table(conn, "ontology_edges"):
            rows = conn.execute(
                """
                SELECT e.edge_id, e.relation_type, e.source_node, e.target_node,
                       e.confidence, e.provenance, n.node_type AS other_type,
                       n.name AS other_name, n.normalized_name AS other_norm
                FROM ontology_edges e
                JOIN ontology_nodes n ON n.node_id =
                    CASE WHEN e.source_node=? THEN e.target_node ELSE e.source_node END
                WHERE e.source_node=? OR e.target_node=?
                ORDER BY e.edge_id
                """,
                (node_id, node_id, node_id),
            ).fetchall()
            for e in rows:
                try:
                    prov = json.loads(e["provenance"] or "[]")
                except json.JSONDecodeError:
                    prov = []
                direction = "out" if int(e["source_node"]) == node_id else "in"
                neighbors.append({
                    "edge_id": int(e["edge_id"]),
                    "relation_type": e["relation_type"],
                    "direction": direction,
                    "other_id": int(e["source_node"] if direction == "out" else e["target_node"]),
                    "other_type": e["other_type"],
                    "other_name": e["other_name"],
                    "confidence": round(float(e["confidence"] or 0), 3),
                    "provenance": prov,
                })
        return {"node": node, "neighbors": neighbors}
    finally:
        conn.close()
