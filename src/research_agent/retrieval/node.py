"""文献检索节点。

对应 LangGraph 中 "retrieval" 节点，支持三种模式：
- mode='search' ：按 query 检索 API → 下载 PDF 入库（BLOB）→ 清洗出精校文本 → 元数据补全；
- mode='skill'  ：按 planner/content 给定的专项 skill 执行证据缺口/扩展/引用溯源；
- mode='enrich' ：质量评估节点发现元数据缺漏后“发回”本节点，仅做元数据补全（不重复下载）；
- mode='load'   ：按 paper_key 直接装载已有记录（用于逐篇处理 / 实时监控入口）。
"""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from typing import Any

from research_agent.config import Settings, settings as default_settings
from research_agent.db import connect, get_paper, log_event, upsert_paper, utcnow
from research_agent.retrieval.api_clients import ApiHub
from research_agent.retrieval.llm import RetrievalLLM
from research_agent.retrieval.pdf_cleaner import clean_pdf
from research_agent.retrieval.skills import (
    SKILL_EVIDENCE_GAP,
    SKILL_QUERY_EXPANSION,
    SKILL_REFERENCE_TRACING,
    derive_expanded_queries,
    filter_relevant_records,
    plan_evidence_gap_queries,
    trace_references,
)

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _process_records(
    records: list[dict],
    *,
    retriever: RetrievalLLM | None,
    api: ApiHub,
    conn: sqlite3.Connection,
    query: str,
    queries: list[str],
) -> dict[str, Any]:
    """把已检索到的候选记录逐篇下载/清洗/入库。"""
    ingested: list[str] = []
    errors: list[dict] = []
    log_event(conn, "retrieval", "search-done",
              details={"query": query, "queries": queries, "hits": len(records),
                       "llm": retriever is not None})
    for rec in records:
        key = rec.get("paper_key")
        try:
            if retriever:
                cleaned = retriever.clean_metadata(rec)
                if cleaned:
                    from research_agent.retrieval.api_clients import merge_metadata

                    rec = merge_metadata(rec, cleaned)
            pdf = api.download_pdf(rec)
            clean = None
            fulltext_source = None
            if pdf:
                clean = clean_pdf(pdf)
                fulltext_source = "pdf"
            rec["pdf_blob"] = pdf
            rec["pdf_size"] = len(pdf) if pdf else None
            rec["pdf_sha256"] = _sha256(pdf) if pdf else None
            rec["clean_text"] = clean["text"] if clean else None
            rec["clean_text_sha256"] = (
                _sha256(clean["text"].encode("utf-8")) if clean and clean["text"] else None
            )
            # 任意源：有 PMCID 时回退 Europe PMC OA XML，再回退摘要
            if not rec.get("clean_text"):
                if rec.get("pmcid") and callable(getattr(api, "fulltext_text", None)):
                    ft = api.fulltext_text(rec["pmcid"])
                    if ft:
                        rec["clean_text"] = ft
                        fulltext_source = "xml"
                if not rec.get("clean_text") and rec.get("abstract"):
                    rec["clean_text"] = rec["abstract"]
                    fulltext_source = "abstract"
            if rec.get("clean_text"):
                rec["clean_text_sha256"] = _sha256(
                    rec["clean_text"].encode("utf-8"))
            rec["fulltext_source"] = fulltext_source
            rec["status"] = "ingested"
            upsert_paper(conn, rec)
            log_event(conn, "retrieval", "paper-ingested", key,
                      {"title": rec.get("title"), "pdf_bytes": rec["pdf_size"],
                       "clean_chars": len(rec["clean_text"] or ""),
                       "fulltext_source": fulltext_source,
                       "removed_blocks": (clean or {}).get("removed_total", 0)})
            ingested.append(key)
        except Exception as exc:  # noqa: BLE001
            logger.exception("处理文献失败 %s", key)
            errors.append({"paper_key": key, "error": str(exc)})
            log_event(conn, "retrieval", "paper-error", key, {"error": str(exc)})
    return {"paper_keys": ingested, "count": len(ingested), "errors": errors}


def apply_topic_relevance_gate(records: list[dict],
                               topic_terms: list[str] | None,
                               *,
                               min_overlap: int = 1) -> tuple[list[dict], list[dict]]:
    """领域相关性硬门：把明显跨域的命中挡在入库之前。

    背景：语料一旦混入跨域论文（例如炔酰胺任务里混进脂质体/疟疾文献），
    后续机制抽取、机会挖掘与候选生成都会被污染，而且很难在消费层补救。

    规则（保守优先，避免误杀）：
    - records 的 title/abstract/keywords 文本过短（< 30 字符）视为不可判定，放行；
    - 长文本记录必须与 topic_terms 有至少 ``min_overlap`` 个词元重叠；
    - 词元按拉丁词（≥4 字符）与中文字符串（≥2 字）切分，忽略停用词。
    """
    if not topic_terms:
        return list(records), []
    tokens = _topic_tokens(topic_terms)
    if not tokens:
        return list(records), []
    kept: list[dict] = []
    dropped: list[dict] = []
    for rec in records:
        text = " ".join([
            str(rec.get("title") or ""),
            str(rec.get("abstract") or ""),
            str(rec.get("keywords") or ""),
        ])
        low = text.lower()
        stripped = low.strip()
        if len(stripped) < 30:
            kept.append(rec)
            continue
        hits = 0
        for token in tokens:
            if token in low:
                hits += 1
                if hits >= min_overlap:
                    break
        if hits >= min_overlap:
            kept.append(rec)
        else:
            dropped.append({**rec, "_gate_reason": "topic_token_overlap_zero"})
    return kept, dropped


_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "that", "this", "these",
    "those", "study", "studies", "using", "used", "based", "novel", "new",
    "review", "research", "results", "analysis", "method", "methods",
    "effect", "effects", "role", "roles", "via", "toward", "towards",
}
_TOKEN_RE = re.compile(r"[a-z]{4,}|[\u4e00-\u9fff]{2,}")


def _topic_tokens(terms: list[str]) -> set[str]:
    tokens: set[str] = set()
    for term in terms:
        for token in _TOKEN_RE.findall(str(term or "").lower()):
            if token in _STOPWORDS:
                continue
            tokens.add(token)
    return tokens


def ingest_search_results(
    query: str,
    max_results: int,
    *,
    api: ApiHub | None = None,
    model=None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    dimensions: list[str] | None = None,
    fixed_queries: list[str] | None = None,
    topic_terms: list[str] | None = None,
) -> dict[str, Any]:
    """LLM/专项 skill 规划检索式 → 逐篇下载 PDF → 清洗 → 入库。

    fixed_queries 提供时不再调用 LLM 规划，直接执行给定检索式。
    topic_terms 提供时先过领域相关性硬门，跨域命中直接丢弃并记入报告。
    """
    settings = settings or default_settings
    api = api or ApiHub()
    retriever = RetrievalLLM(model) if model is not None else None
    own_conn = conn is None
    conn = conn or connect(settings.db_path)
    try:
        queries = fixed_queries or (
            retriever.plan_queries(query, dimensions) if retriever else [query])
        records: list[dict] = []
        seen: set[str] = set()
        per_query = max(1, max_results // max(1, len(queries)))
        for q in queries:
            for rec in api.search(q, max_results=per_query):
                key = rec.get("paper_key")
                if key in seen:
                    continue
                seen.add(key)
                records.append(rec)
                if len(records) >= max_results:
                    break
            if len(records) >= max_results:
                break
        if not records:
            records = api.search(query, max_results=max_results)
        gate_terms = list(topic_terms or []) or [query, *(dimensions or [])]
        records, dropped = apply_topic_relevance_gate(records, gate_terms)
        out = _process_records(
            records,
            retriever=retriever,
            api=api,
            conn=conn,
            query=query,
            queries=queries,
        )
        if dropped:
            out["relevance_gate"] = {
                "dropped": len(dropped),
                "topics": gate_terms[:6],
                "examples": [
                    {"paper_key": d.get("paper_key"), "title": d.get("title")}
                    for d in dropped[:5]
                ],
            }
            log_event(conn, "retrieval", "relevance-gate-dropped", None,
                      out["relevance_gate"])
        return out
    finally:
        if own_conn:
            conn.close()


def ingest_existing_records(
    records: list[dict],
    *,
    api: ApiHub | None = None,
    model=None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    query: str = "",
) -> dict[str, Any]:
    """直接入库 skill 已取回/已筛选的候选（如引用列表），不重复执行检索。"""
    settings = settings or default_settings
    api = api or ApiHub()
    retriever = RetrievalLLM(model) if model is not None else None
    own_conn = conn is None
    conn = conn or connect(settings.db_path)
    try:
        return _process_records(
            list(records),
            retriever=retriever,
            api=api,
            conn=conn,
            query=query,
            queries=[query] if query else ["reference-trace"],
        )
    finally:
        if own_conn:
            conn.close()


def enrich_existing_paper(
    paper_key: str,
    *,
    api: ApiHub | None = None,
    model=None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """对已有记录做元数据补全（arXiv/OpenAlex/Crossref + LLM 规整）。"""
    settings = settings or default_settings
    api = api or ApiHub()
    retriever = RetrievalLLM(model) if model is not None else None
    own_conn = conn is None
    conn = conn or connect(settings.db_path)
    try:
        rec = get_paper(conn, paper_key)
        if not rec:
            return {"paper_key": paper_key, "ok": False, "reason": "not-found"}
        preserved = {
            k: rec.get(k) for k in
            ("pdf_blob", "pdf_sha256", "pdf_size", "clean_text",
             "clean_text_sha256", "status", "abstract", "pub_year",
             "pub_date", "publication_status", "pmcid", "fulltext_source",
             "paper_key")
        }
        rec.pop("pdf_blob", None)
        rec.pop("clean_text", None)
        enriched = api.enrich(rec)
        if retriever:
            cleaned = retriever.clean_metadata(enriched)
            if cleaned:
                from research_agent.retrieval.api_clients import merge_metadata

                enriched = merge_metadata(enriched, cleaned)
        before = {
            "authors": len(rec.get("authors") or []),
            "affs": sum(len(a.get("affiliations") or []) for a in (rec.get("authors") or [])),
            "doi": rec.get("doi"), "venue": rec.get("venue"),
        }
        after = {
            "authors": len(enriched.get("authors") or []),
            "affs": sum(len(a.get("affiliations") or []) for a in (enriched.get("authors") or [])),
            "doi": enriched.get("doi"), "venue": enriched.get("venue"),
        }
        changed = before != after
        # 保留已入库的 PDF/清洗文本，仅更新元数据
        for field, value in preserved.items():
            enriched[field] = value
        upsert_paper(conn, enriched)
        log_event(conn, "retrieval", "enrich-done", paper_key,
                  {"before": before, "after": after, "changed": changed})
        return {"paper_key": paper_key, "ok": True, "changed": changed,
                "before": before, "after": after}
    finally:
        if own_conn:
            conn.close()


def load_paper_summary(
    paper_key: str,
    *,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """装载论文概要（不含 BLOB/全文），供图状态流转使用。"""
    settings = settings or default_settings
    own_conn = conn is None
    conn = conn or connect(settings.db_path)
    try:
        rec = get_paper(conn, paper_key)
        if not rec:
            return None
        rec.pop("pdf_blob", None)
        rec.pop("clean_text", None)
        return rec
    finally:
        if own_conn:
            conn.close()


def make_retrieval_node(api: ApiHub | None = None,
                        model=None,
                        conn: sqlite3.Connection | None = None,
                        settings: Settings | None = None):
    """构造 LangGraph 检索节点。支持 broad 检索和专项 skill 调用。"""
    settings = settings or default_settings

    def retrieval_node(state: dict) -> dict:
        mode = state.get("mode", "search")
        key = state.get("current_key")
        if mode == "skill":
            query = (state.get("query") or state.get("topic") or "").strip()
            max_results = int(state.get("max_results") or 5)
            skill = str(state.get("retrieval_skill") or "").strip()
            if skill == SKILL_EVIDENCE_GAP:
                plan_rows = plan_evidence_gap_queries(
                    state.get("edge_gaps"),
                    topic=query,
                    model=model,
                    max_queries=max(1, min(10, max_results)),
                )
                if not plan_rows:
                    return {"error": "证据缺口为空或未生成检索式", "status": "no-paper"}
                res = ingest_search_results(
                    query, max_results, api=api, model=model,
                    conn=conn, settings=settings,
                    fixed_queries=[str(p.get("query") or "") for p in plan_rows
                                   if str(p.get("query") or "").strip()],
                )
            elif skill == SKILL_QUERY_EXPANSION:
                expanded = derive_expanded_queries(
                    state.get("expanded_records") or state.get("paper_keys") or [],
                    topic_terms=[query] + [str(x) for x in
                                           state.get("topic_terms") or []],
                    model=model,
                    max_queries=max_results,
                )
                if not expanded:
                    return {"error": "无法从首轮结果生成受控扩展检索式",
                            "status": "no-paper"}
                res = ingest_search_results(
                    query, max_results, api=api, model=model,
                    conn=conn, settings=settings,
                    fixed_queries=[str(p.get("query") or "") for p in expanded],
                )
            elif skill == SKILL_REFERENCE_TRACING:
                seeds = [str(x) for x in state.get("seed_paper_keys") or []
                         if str(x).strip()]
                direction = str(state.get("reference_direction") or "both").lower()

                def seed_fetcher(seed_key: str) -> list[dict]:
                    seed = {"paper_key": seed_key}
                    if conn is not None:
                        rec = get_paper(conn, seed_key)
                        if rec:
                            rec.pop("pdf_blob", None)
                            rec.pop("clean_text", None)
                            seed = rec
                    rows: list[dict] = []
                    fetch_refs = getattr(api, "fetch_references", None)
                    fetch_cits = getattr(api, "fetch_citations", None)
                    if direction in ("backward", "both") and callable(fetch_refs):
                        rows.extend(fetch_refs(seed) or [])
                    if direction in ("forward", "both") and callable(fetch_cits):
                        rows.extend(fetch_cits(seed) or [])
                    return rows

                traced = trace_references(
                    seeds,
                    fetcher=seed_fetcher if callable(getattr(api, "fetch_references", None))
                    else None,
                    topic_terms=[query] + [str(x) for x in
                                           state.get("topic_terms") or []],
                    required_terms=state.get("required_terms"),
                    max_depth=max(1, int(state.get("reference_depth") or 1)),
                    max_results=max(1, max_results * 5),
                )
                candidates = filter_relevant_records(
                    traced["candidates"],
                    topic_terms=[query],
                    required_terms=state.get("required_terms"),
                    max_results=max_results,
                )
                res = ingest_existing_records(
                    candidates, api=api, model=model,
                    conn=conn, settings=settings, query=query,
                )
                res["trace_report"] = traced["report"]
            else:
                return {"error": f"未知检索 skill: {skill}", "status": "error"}
            keys = res["paper_keys"]
            current = keys[0] if keys else None
            return {
                "paper_keys": keys,
                "current_key": current,
                "retrieval_report": res,
                "mode": "quality",
                "status": "search-done" if current else "no-paper",
                "error": res["errors"][0]["error"] if res["errors"] and not current else None,
            }
        if mode == "search":
            query = (state.get("query") or "").strip()
            if not query:
                return {"error": "query 为空，无法检索", "status": "error"}
            res = ingest_search_results(
                query, int(state.get("max_results") or 5),
                api=api, model=model, conn=conn, settings=settings,
            )
            keys = res["paper_keys"]
            current = keys[0] if keys else None
            return {
                "paper_keys": keys,
                "current_key": current,
                "retrieval_report": res,
                "mode": "quality",      # 进入质量评估
                "status": "search-done" if current else "no-paper",
                "error": res["errors"][0]["error"] if res["errors"] and not current else None,
            }
        if mode == "enrich":
            if not key:
                return {"error": "缺少 paper_key，无法补全", "status": "error"}
            res = enrich_existing_paper(key, api=api, model=model,
                                        conn=conn, settings=settings)
            return {"enrich_report": res, "mode": "quality",
                    "status": "enriched" if res.get("ok") else "enrich-failed",
                    "error": None if res.get("ok") else res.get("reason")}
        if mode == "load":
            if not key:
                return {"error": "缺少 paper_key", "status": "error"}
            rec = load_paper_summary(key, conn=conn, settings=settings)
            if not rec:
                return {"error": f"文献不存在: {key}", "status": "error"}
            return {"paper_record": rec, "mode": "quality",
                    "status": "loaded"}
        return {"error": f"未知模式: {mode}", "status": "error"}

    return retrieval_node
