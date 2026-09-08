"""文献检索节点。

对应 LangGraph 中 "retrieval" 节点，支持三种模式：
- mode='search' ：按 query 检索 API → 下载 PDF 入库（BLOB）→ 清洗出精校文本 → 元数据补全；
- mode='enrich' ：质量评估节点发现元数据缺漏后“发回”本节点，仅做元数据补全（不重复下载）；
- mode='load'   ：按 paper_key 直接装载已有记录（用于逐篇处理 / 实时监控入口）。
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from typing import Any

from research_agent.config import Settings, settings as default_settings
from research_agent.db import connect, get_paper, log_event, upsert_paper, utcnow
from research_agent.retrieval.api_clients import ApiHub
from research_agent.retrieval.llm import RetrievalLLM
from research_agent.retrieval.pdf_cleaner import clean_pdf

logger = logging.getLogger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_search_results(
    query: str,
    max_results: int,
    *,
    api: ApiHub | None = None,
    model=None,
    conn: sqlite3.Connection | None = None,
    settings: Settings | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    """LLM(DeepSeek V4) 规划检索式 → 逐篇下载 PDF → 清洗 → 入库。

    model 为 None 时退化为「用原始 query 检索」的确定性实现。
    """
    settings = settings or default_settings
    api = api or ApiHub()
    retriever = RetrievalLLM(model) if model is not None else None
    own_conn = conn is None
    conn = conn or connect(settings.db_path)
    ingested: list[str] = []
    errors: list[dict] = []
    try:
        queries = retriever.plan_queries(query, dimensions) if retriever else [query]
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
        log_event(conn, "retrieval", "search-done",
                  details={"query": query, "queries": queries, "hits": len(records),
                           "llm": model is not None})
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
                    if rec.get("pmcid") \
                            and callable(getattr(api, "fulltext_text", None)):
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
    finally:
        if own_conn:
            conn.close()
    return {"paper_keys": ingested, "count": len(ingested), "errors": errors}


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
    """构造 LangGraph 检索节点。model 为绑定 DeepSeek V4 的 ChatModel。"""
    settings = settings or default_settings

    def retrieval_node(state: dict) -> dict:
        mode = state.get("mode", "search")
        key = state.get("current_key")
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
