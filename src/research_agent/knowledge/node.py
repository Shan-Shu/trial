"""知识提取节点：文本预处理 → LLM 抽取（实体/关系/属性/事件）→ 写入动态本体。"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

from research_agent.config import Settings, settings as default_settings
from research_agent.db import connect, get_paper, get_quality_result, log_event
from research_agent.knowledge.extractor import (
    KnowledgeExtractor,
    blend_confidence,
    is_reporting_phrase,
)
from research_agent.knowledge.preprocess import (
    chunk_paragraphs,
    split_paragraphs,
    split_sentences,
)
from research_agent.ontology.store import (
    get_node_id,
    graph_summary,
    init_ontology,
    record_ontology_run,
    upsert_edge,
    upsert_node,
)

logger = logging.getLogger(__name__)


def _lookup_any_type(conn: sqlite3.Connection, name: str) -> int | None:
    """按规范化名称在全部类型中查找既有节点（关系对象可能未在本文声明）。"""
    from research_agent.ontology.store import _norm

    row = conn.execute(
        "SELECT node_id FROM ontology_nodes WHERE normalized_name=? LIMIT 1",
        (_norm(name),),
    ).fetchone()
    return int(row["node_id"]) if row else None


def _upsert_knowledge(conn: sqlite3.Connection, data: dict[str, Any], *,
                      quality_q: float | None, flagged: bool,
                      paper_key: str, settings: Settings) -> dict[str, Any]:
    """把一次抽取结果写入本体，返回统计（新增节点/边/类型）。"""
    stats = {"entities": 0, "relations": 0, "events": 0,
             "new_nodes": 0, "new_edges": 0, "new_types": [],
             "dropped_garbage": 0}
    name_to_id: dict[tuple[str, str], int] = {}

    for e in data.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "").strip()
        if not name:
            continue
        if is_reporting_phrase(name):
            stats["dropped_garbage"] += 1
            continue
        ntype = str(e.get("type") or "Concept").strip() or "Concept"
        model_conf = e.get("confidence")
        try:
            model_conf = float(model_conf) if model_conf is not None else 0.5
        except (TypeError, ValueError):
            model_conf = 0.5
        conf = blend_confidence(model_conf, quality_q, flagged, settings)
        aliases = [str(a) for a in (e.get("aliases") or []) if str(a).strip()]
        attrs = e.get("attributes") if isinstance(e.get("attributes"), dict) else {}
        prov = [{"paper": paper_key, "evidence": str(e.get("evidence") or "")[:500]}]
        node_id, is_new = upsert_node(
            conn, node_type=ntype, name=name, confidence=conf,
            aliases=aliases, attributes=attrs, provenance=prov,
        )
        name_to_id[(ntype, name.lower())] = node_id
        name_to_id[("*", name.lower())] = node_id
        stats["entities"] += 1
        stats["new_nodes"] += int(is_new)

    def _resolve(name: str) -> int | None:
        if not name:
            return None
        key = ("*", name.lower().strip())
        if key in name_to_id:
            return name_to_id[key]
        return _lookup_any_type(conn, name)

    for r in data.get("relations") or []:
        if not isinstance(r, dict):
            continue
        src = _resolve(str(r.get("subject") or ""))
        tgt = _resolve(str(r.get("object") or ""))
        if src is None or tgt is None:
            continue
        rtype = str(r.get("type") or "related_to").strip() or "related_to"
        model_conf = r.get("confidence")
        try:
            model_conf = float(model_conf) if model_conf is not None else 0.5
        except (TypeError, ValueError):
            model_conf = 0.5
        conf = blend_confidence(model_conf, quality_q, flagged, settings)
        attrs = {"predicate": str(r.get("predicate") or "")}
        prov = [{"paper": paper_key, "evidence": str(r.get("evidence") or "")[:500]}]
        _, is_new = upsert_edge(
            conn, relation_type=rtype, src_id=src, tgt_id=tgt,
            confidence=conf, attributes=attrs, provenance=prov,
        )
        stats["relations"] += 1
        stats["new_edges"] += int(is_new)

    for ev_idx, ev in enumerate(data.get("events") or []):
        if not isinstance(ev, dict):
            continue
        trigger = str(ev.get("trigger") or "").strip()
        etype = str(ev.get("type") or "Event").strip() or "Event"
        name = trigger[:100] or f"{etype}:{ev_idx}"
        if is_reporting_phrase(name):
            stats["dropped_garbage"] += 1
            continue
        try:
            model_conf = float(ev.get("confidence") or 0.5)
        except (TypeError, ValueError):
            model_conf = 0.5
        conf = blend_confidence(model_conf, quality_q, flagged, settings)
        attrs = {"time": ev.get("time")}
        if isinstance(ev.get("attributes"), dict):
            attrs.update(ev["attributes"])
        prov = [{"paper": paper_key, "evidence": str(ev.get("evidence") or "")[:500]}]
        event_id, ev_is_new = upsert_node(
            conn, node_type=etype, name=name, confidence=conf,
            aliases=[], attributes=attrs, provenance=prov,
        )
        stats["events"] += 1
        stats["new_nodes"] += int(ev_is_new)
        for participant in (ev.get("participants") or []):
            pid = _resolve(str(participant))
            if pid is None:
                continue
            _, e_new = upsert_edge(
                conn, relation_type="involves", src_id=pid, tgt_id=event_id,
                confidence=conf, attributes={}, provenance=prov,
            )
            stats["new_edges"] += int(e_new)
    return stats


def make_knowledge_node(model=None,
                        conn: sqlite3.Connection | None = None,
                        settings: Settings | None = None):
    """构造 LangGraph 知识提取节点。model 为 None 时跳过抽取（仅统计预处理）。"""
    settings = settings or default_settings

    def knowledge_node(state: dict) -> dict:
        key = state.get("current_key")
        if not key:
            return {"error": "缺少 current_key", "status": "error"}
        own_conn = conn is None
        db = conn or connect(settings.db_path)
        try:
            init_ontology(db)
            rec = get_paper(db, key)
            if not rec:
                return {"error": f"文献不存在: {key}", "status": "error"}
            qres = get_quality_result(db, key) or {}
            flagged = bool(state.get("needs_review") or qres.get("needs_review"))
            quality_q = qres.get("quality")
            text = rec.get("clean_text") or ""

            paragraphs = split_paragraphs(text)
            sentences = split_sentences(text)
            chunks = chunk_paragraphs(
                paragraphs, settings.max_extract_chars, settings.max_extract_chunks
            )
            pre_stats = {
                "clean_chars": len(text),
                "paragraphs": len(paragraphs),
                "sentences": len(sentences),
                "chunks": len(chunks),
            }
            totals: dict[str, Any] = {"entities": 0, "relations": 0, "events": 0,
                                      "new_nodes": 0, "new_edges": 0, "new_types": [],
                                      "dropped_garbage": 0}
            meta = {
                "title": rec.get("title"), "venue": rec.get("venue"),
                "pub_year": rec.get("pub_year"), "doi": rec.get("doi"),
            }
            if model is None:
                log_event(db, "knowledge", "skipped-no-model", key, pre_stats)
                return {"extraction_report": {"preprocess": pre_stats,
                                              "skipped": "model 未配置"},
                        "status": "extracted"}

            extractor = KnowledgeExtractor(model, settings)
            existing_entities = [
                r["label"] for r in db.execute(
                    """
                    SELECT n.node_type || ': ' || n.name AS label
                    FROM ontology_nodes n
                    WHERE EXISTS (
                        SELECT 1 FROM ontology_edges e
                        WHERE e.source_node = n.node_id OR e.target_node = n.node_id
                    )
                    ORDER BY n.last_seen_at DESC LIMIT 150
                    """
                )
            ]
            before_types = {
                r["type_key"]
                for r in db.execute("SELECT type_key FROM ontology_type_registry")
            }
            for chunk in chunks:
                data = extractor.extract(chunk, meta, existing_entities)
                chunk_stats = _upsert_knowledge(
                    db, data, quality_q=quality_q, flagged=flagged,
                    paper_key=key, settings=settings,
                )
                for k in ("entities", "relations", "events", "new_nodes",
                          "new_edges", "dropped_garbage"):
                    totals[k] += chunk_stats[k]
            db.commit()
            after_types = {
                r["type_key"]
                for r in db.execute("SELECT type_key FROM ontology_type_registry")
            }
            totals["new_types"] = sorted(after_types - before_types)
            record_ontology_run(db, key, totals, totals["new_types"])
            summary = graph_summary(db)
            report = {"preprocess": pre_stats, "extracted": totals,
                      "ontology": summary}
            log_event(db, "knowledge", "extracted", key, report)
            # 注意：不覆盖顶层 decision（knowledge/flagged 由质量节点给出）
            return {"extraction_report": report, "status": "extracted"}
        finally:
            if own_conn:
                db.close()

    return knowledge_node
