"""知识消费节点（确定性服务，不直接由 LLM 查数据库）。

职责：
1. 从动态本体中挖掘高可信模式卡与证据卡；
2. 语料不足时生成 retrieval_request；
3. 若配置了 collector，则调用现有 retrieval->quality->knowledge 流水线
   补齐语料后重新挖掘。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Callable

from research_agent.config import Settings, settings as default_settings
from research_agent.db import connect
from research_agent.domains import normalize_domain_profile
from research_agent.ontology import store as ont
from research_agent.retrieval.skills import (
    build_skill_request,
    normalize_edge_gaps,
)
from research_agent.study.events import log_study_event
from research_agent.study.json_utils import clean_str

logger = logging.getLogger(__name__)


def _json_list(raw: Any) -> list:
    if raw in (None, ""):
        return []
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _relevance(terms: list[str], text: str) -> int:
    low = (text or "").lower()
    score = 0
    for term in terms:
        t = (term or "").strip().lower()
        if t and t in low:
            score += len(t)
    return score


def mine_ontology_evidence(conn: sqlite3.Connection,
                           mission: dict[str, Any],
                           limit_patterns: int = 200,
                           limit_evidence: int = 500) -> dict[str, Any]:
    """从 ontology_edges + provenance 中生成可追溯模式卡与证据卡。"""
    ont.init_ontology(conn)
    summary = ont.graph_summary(conn)
    paper_count = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
    min_conf = float(mission.get("min_confidence") or 0.6)
    terms = [clean_str(t) for t in mission.get("seed_terms") or [] if clean_str(t)]
    rows = conn.execute(
        """
        SELECT e.edge_id, e.relation_type, e.source_node, e.target_node,
               e.confidence, e.provenance, e.evidence_tier,
               sn.node_type AS source_type, sn.name AS source_name,
               tn.node_type AS target_type, tn.name AS target_name
        FROM ontology_edges e
        JOIN ontology_nodes sn ON sn.node_id = e.source_node
        JOIN ontology_nodes tn ON tn.node_id = e.target_node
        WHERE e.confidence >= ?
        ORDER BY e.confidence DESC, e.edge_id
        """,
        (min_conf,),
    ).fetchall()
    patterns: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    all_papers: set[str] = set()
    for r in rows:
        pattern_id = f"P-{int(r['edge_id']):04d}"
        prov = _json_list(r["provenance"])
        paper_keys: list[str] = []
        for p in prov:
            if isinstance(p, dict) and clean_str(p.get("paper")):
                key = clean_str(p["paper"])
                if key not in paper_keys:
                    paper_keys.append(key)
                all_papers.add(key)
        source_label = f"{r['source_type']}: {r['source_name']}"
        target_label = f"{r['target_type']}: {r['target_name']}"
        title = f"{source_label} --{r['relation_type']}--> {target_label}"
        score = _relevance(terms, title) + _relevance(terms, r["source_name"]) \
            + _relevance(terms, r["target_name"])
        pattern = {
            "pattern_id": pattern_id,
            "title": title,
            "source_type": r["source_type"],
            "source_name": r["source_name"],
            "relation_type": r["relation_type"],
            "target_type": r["target_type"],
            "target_name": r["target_name"],
            "support_count": len(paper_keys),
            "confidence": round(float(r["confidence"] or 0), 3),
            "evidence_tier": r["evidence_tier"] or "unclassified",
            "paper_keys": paper_keys,
            "evidence_ids": [],
            "relevance": score,
        }
        for pi, p in enumerate(prov):
            if not isinstance(p, dict):
                continue
            evidence_id = f"E-{int(r['edge_id']):04d}-{pi + 1}"
            sentence = clean_str(p.get("evidence"), "")
            if not sentence:
                continue
            evidence.append({
                "evidence_id": evidence_id,
                "pattern_id": pattern_id,
                "source": "ontology",
                "paper_key": clean_str(p.get("paper"), "unknown"),
                "sentence": sentence,
                "confidence": round(float(r["confidence"] or 0), 3),
                "evidence_tier": r["evidence_tier"] or "unclassified",
            })
            pattern["evidence_ids"].append(evidence_id)
        patterns.append(pattern)
    if terms:
        patterns.sort(key=lambda x: x["relevance"], reverse=True)
    patterns = [p for p in patterns if p["evidence_ids"]]
    patterns = patterns[:limit_patterns]
    pattern_ids = {p["pattern_id"] for p in patterns}
    evidence = [e for e in evidence if e["pattern_id"] in pattern_ids][:limit_evidence]

    year_rows: dict[str, Any] = {}
    if all_papers:
        ph = ",".join("?" * len(all_papers))
        year_rows = {
            r["paper_key"]: r["pub_year"]
            for r in conn.execute(
                f"SELECT paper_key, pub_year FROM papers "
                f"WHERE paper_key IN ({ph})", tuple(all_papers))
        }
    for e in evidence:
        e["year"] = year_rows.get(e["paper_key"])
    coverage = 0.0
    if patterns:
        avg_support = sum(p["support_count"] for p in patterns) / len(patterns)
        coverage = round(min(1.0, max(
            len(patterns) / max(1, int(mission.get("min_patterns") or 5)),
            avg_support / 3.0,
        )), 3)
    return {
        "corpus": {
            "papers": paper_count,
            **summary,
            "paper_keys": sorted(all_papers),
        },
        "patterns": patterns,
        "evidence": evidence,
        "coverage_score": coverage,
    }


def build_retrieval_request(plan: dict[str, Any] | None,
                            edge_gaps: Any = None) -> dict[str, Any]:
    plan = plan or {}
    mission = plan.get("mission") or {}
    terms = [clean_str(t) for t in mission.get("seed_terms") or [] if clean_str(t)]
    if not terms:
        terms = [clean_str(plan.get("domain"), "research")]
    request = {
        "reason": "当前本体/本地语料中没有达到最低阈值的可溯源模式",
        "seed_terms": terms,
        "max_results": int(mission.get("max_results") or 80),
        "min_confidence": float(mission.get("min_confidence") or 0.6),
        "domain_profile": normalize_domain_profile(
            plan.get("domain_profile"), plan.get("domain"),
            plan.get("goal") or ""),
        "suggested_route": "retrieval -> quality -> knowledge",
    }
    if edge_gaps:
        request["edge_gaps"] = normalize_edge_gaps(edge_gaps)
        request["reason"] = "内容节点识别出需要补强的低支持本体边"
    return build_skill_request(plan, edge_gaps=request.get("edge_gaps"))


def build_design_context(knowledge: dict[str, Any],
                         plan: dict[str, Any] | None = None) -> dict[str, Any]:
    """从消费到的模式/证据中提取通用“可组合设计素材”。"""
    patterns = knowledge.get("patterns") or []
    evidence = knowledge.get("evidence") or []
    component_type_count: dict[str, int] = {}
    components: dict[str, dict[str, Any]] = {}
    relation_count: dict[str, int] = {}
    for p in patterns:
        for side, typ, name in (
                ("source", p.get("source_type"), p.get("source_name")),
                ("target", p.get("target_type"), p.get("target_name"))):
            component_type_count[typ] = component_type_count.get(typ, 0) + 1
            key = f"{typ}:{name}"
            comp = components.setdefault(key, {
                "type": typ, "name": name, "relation_degree": 0,
                "pattern_ids": [], "evidence_ids": [],
            })
            comp["relation_degree"] += 1
            comp["pattern_ids"].append(p["pattern_id"])
        relation_count[p["relation_type"]] = relation_count.get(
            p["relation_type"], 0) + 1
    evidence_by_pattern: dict[str, list[str]] = {}
    for e in evidence:
        evidence_by_pattern.setdefault(e["pattern_id"], []).append(e["evidence_id"])
    for p in patterns:
        for comp_key in (
                f"{p.get('source_type')}:{p.get('source_name')}",
                f"{p.get('target_type')}:{p.get('target_name')}"):
            if comp_key in components:
                components[comp_key]["evidence_ids"].extend(
                    evidence_by_pattern.get(p["pattern_id"], []))
    weak_count = sum(1 for p in patterns if int(p.get("support_count") or 0) < 3)
    return {
        "objective_hint": clean_str((plan or {}).get("goal")),
        "component_types": component_type_count,
        "composable_relations": relation_count,
        "candidate_components": sorted(
            list(components.values()),
            key=lambda c: (c["relation_degree"], len(c["evidence_ids"])),
            reverse=True,
        )[:120],
        "known_limits": {
            "weak_patterns": weak_count,
            "strong_patterns": len(patterns) - weak_count,
        },
        "combination_space_hint": "将不同 component_type 中的候选组件与 "
                                  "composable_relations 中关系进行组合/替换/迁移。",
    }


def make_knowledge_consumer_node(conn: sqlite3.Connection | None = None,
                                 settings: Settings | None = None,
                                 collector: Callable[[dict[str, Any]], dict] | None = None):
    """构造 LangGraph 知识消费节点。

    collector 接收 retrieval_request，调用现有流水线补充语料并返回
    {"count": int, "errors": [...]} 等汇总。未配置时仅发出检索请求。
    """
    settings = settings or default_settings

    def knowledge_consumer_node(state: dict) -> dict:
        plan = state.get("plan") or {}
        mission = plan.get("mission") or {}
        run_id = state.get("run_id")
        own_conn = conn is None
        db = conn or connect(settings.db_path)
        try:
            log_study_event(
                conn, settings, "knowledge_consumer", run_id, "running",
                {"domain": plan.get("domain")})
            bundle = mine_ontology_evidence(db, mission)
            collection_report = state.get("collection_report") or {}
            force_collect = bool(state.get("force_collect"))
            edge_gaps = state.get("edge_gaps")
            gap_done = bool(state.get("gap_retrieval_done"))
            plan_retrieval = plan.get("retrieval") or {}
            evidence_gap_requested = bool(
                plan_retrieval.get("evidence_gap_enabled")
                or plan_retrieval.get("strategy") == "evidence_gap"
            )
            collect_gaps = bool(state.get("collect_gaps")) or bool(
                edge_gaps and evidence_gap_requested and not gap_done
            )
            should_collect = force_collect or collect_gaps or not bundle["patterns"]
            if should_collect and collector is not None:
                try:
                    collection_report = collector(
                        build_retrieval_request(
                            plan,
                            edge_gaps=edge_gaps if collect_gaps else None,
                        )
                    ) or {}
                    bundle = mine_ontology_evidence(db, mission)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("补充语料失败: %s", exc)
                    collection_report["error"] = str(exc)
            if collect_gaps:
                gap_done = True
            if not bundle["patterns"]:
                log_study_event(
                    conn, settings, "knowledge_consumer", run_id,
                    "needs_collection",
                    {"patterns": 0, "evidence": 0,
                     "reason": (build_retrieval_request(plan) or {}).get("reason")})
                return {
                    "knowledge": bundle,
                    "collection_report": collection_report,
                    "retrieval_request": build_retrieval_request(plan),
                    "force_collect": False,
                    "collect_gaps": False,
                    "gap_retrieval_done": gap_done,
                    "status": "needs_collection",
                }
            bundle["design_context"] = build_design_context(bundle, plan)
            log_study_event(
                conn, settings, "knowledge_consumer", run_id, "done",
                {
                    "patterns": len(bundle.get("patterns") or []),
                    "evidence": len(bundle.get("evidence") or []),
                    "coverage_score": bundle.get("coverage_score"),
                    "papers": (bundle.get("corpus") or {}).get("papers"),
                    "collected": (collection_report or {}).get("count"),
                })
            return {
                "knowledge": bundle,
                "collection_report": collection_report,
                "retrieval_request": None,
                "force_collect": False,
                "collect_gaps": False,
                "gap_retrieval_done": gap_done,
                "status": "consumed",
            }
        finally:
            if own_conn:
                db.close()

    return knowledge_consumer_node
