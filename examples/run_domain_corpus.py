# -*- coding: utf-8 -*-
"""按领域包建语料库：多检索式抓取 → 入库 → 质量 → 知识提取（并行）。

设计目标（"每个新研究领域 = 一个 pack"）：
  检索式写在 ``packs/domains/<kind>/corpus_queries.json``，领域画像/超边 schema 写在
  同目录 ``domain.json``，本脚本只做通用编排，不含任何学科内容。

用法::

    # 1) 抓取（arXiv 全文优先）
    python examples/run_domain_corpus.py --db data/hqc_code_based_crypto.db \
        --domain code_based_crypto --source arxiv \
        --per-query 15 --min-papers 300

    # 2) 知识提取（并行，flash 提速）
    python examples/run_domain_corpus.py --db data/hqc_code_based_crypto.db \
        --domain code_based_crypto --extract-only --workers 8 --model flash

    # 3) 只看统计
    python examples/run_domain_corpus.py --db data/hqc_code_based_crypto.db --stats-only
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from research_agent import packs
from research_agent.config import Settings
from research_agent.db import connect, log_event, meta_get, meta_set
from research_agent.domains import normalize_domain_profile
from research_agent.knowledge.node import make_knowledge_node
from research_agent.models import build_chat_model, build_role_model
from research_agent.ontology.store import init_ontology
from research_agent.quality.node import make_quality_node
from research_agent.retrieval.api_clients import ApiHub
from research_agent.retrieval.node import ingest_search_results

logger = logging.getLogger("run_domain_corpus")


def load_queries(domain: str, source: str,
                 queries_file: str | None = None) -> list[str]:
    path = Path(queries_file) if queries_file else None
    if path is None:
        base = packs.domain_dir(domain)
        if base is None:
            raise SystemExit(f"[abort] 找不到领域包 {domain}")
        path = base / "corpus_queries.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    sets = data.get("source_sets") or {}
    queries = sets.get(source)
    if not queries:
        # 未声明分源检索式时用扁平列表
        queries = data.get("queries") or []
    if not queries:
        raise SystemExit(f"[abort] {path} 里没有 {source} 的检索式")
    return [str(q) for q in queries]


def build_profile(domain: str, topic: str) -> dict:
    return normalize_domain_profile({"domain_kind": domain}, domain, topic)


def corpus_filter_terms(domain: str) -> list[list[str]]:
    """领域包声明的语料硬门分组（每组至少命中一个词，见 corpus_filter）。"""
    data = packs.domain_data(domain)
    spec = data.get("corpus_filter") if isinstance(data.get("corpus_filter"), dict) \
        else {}
    groups = []
    for group in spec.get("groups_all") or []:
        terms = [str(x).strip().lower() for x in group if str(x).strip()]
        if terms:
            groups.append(terms)
    return groups


def in_scope(text: str, groups: list[list[str]]) -> bool:
    """每个分组都要命中至少一个词；未声明分组时一律视为在范围内。"""
    if not groups:
        return True
    lowered = str(text or "").lower()
    return all(any(term in lowered for term in group) for group in groups)


def mark_out_of_scope(db: Path, domain: str) -> dict:
    """把不满足语料硬门的论文标记为 out_of_scope（不参与提取与统计）。"""
    groups = corpus_filter_terms(domain)
    if not groups:
        return {"checked": 0, "dropped": 0}
    conn = connect(db)
    try:
        rows = conn.execute(
            "SELECT paper_key, title, abstract, clean_text, status FROM papers"
        ).fetchall()
        dropped = []
        for r in rows:
            if r["status"] == "out_of_scope":
                continue
            blob = " ".join(str(r[k] or "") for k in
                            ("title", "abstract", "clean_text"))
            if not in_scope(blob, groups):
                conn.execute("UPDATE papers SET status='out_of_scope' "
                             "WHERE paper_key=?", (r["paper_key"],))
                dropped.append(r["paper_key"])
        if dropped:
            log_event(conn, "retrieval", "corpus-out-of-scope", None,
                      {"count": len(dropped), "sample": dropped[:10]})
        conn.commit()
        return {"checked": len(rows), "dropped": len(dropped),
                "sample": dropped[:10]}
    finally:
        conn.close()


def corpus_stats(db: Path) -> dict:
    conn = connect(db)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status<>'out_of_scope'"
        ).fetchone()[0]
        excluded = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE status='out_of_scope'"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT COALESCE(fulltext_source, 'none') AS src, COUNT(*) AS c "
            "FROM papers WHERE status<>'out_of_scope' "
            "GROUP BY src ORDER BY c DESC").fetchall()
        by_source = {r["src"]: r["c"] for r in rows}
        chars = conn.execute(
            "SELECT COALESCE(SUM(length(COALESCE(clean_text, ''))), 0) "
            "FROM papers WHERE status<>'out_of_scope'").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM ontology_runs").fetchone()[0]
        nodes = conn.execute("SELECT COUNT(*) FROM ontology_nodes").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM ontology_edges").fetchone()[0]
        hyper = conn.execute(
            "SELECT hyperedge_type, COUNT(*) AS c FROM ontology_hyperedges "
            "GROUP BY hyperedge_type ORDER BY c DESC").fetchall()
        conditions = conn.execute(
            "SELECT COUNT(*) FROM ontology_hyperedge_conditions").fetchone()[0]
        measurements = conn.execute(
            "SELECT COUNT(*) FROM ontology_hyperedge_measurements").fetchone()[0]
        return {
            "papers": total, "out_of_scope": excluded,
            "done_queries": len(meta_get(conn, "corpus_done_queries", []) or []),
            "by_fulltext_source": by_source,
            "clean_chars": chars, "extracted_runs": runs, "nodes": nodes,
            "edges": edges, "hyperedges": sum(r["c"] for r in hyper),
            "hyperedge_types": {r["hyperedge_type"]: r["c"] for r in hyper},
            "conditions": conditions, "measurements": measurements,
        }
    finally:
        conn.close()


def print_stats(stats: dict, title: str = "统计") -> None:
    print(f"\n=== {title} ===", flush=True)
    print(f"在范围内论文 {stats['papers']} 篇（另有 {stats.get('out_of_scope', 0)} 篇"
          f"被判为界外）| 已完成检索式 {stats.get('done_queries', 0)} 个 | "
          f"正文字符 {stats['clean_chars']:,} | "
          f"已提取 {stats['extracted_runs']} 篇", flush=True)
    print(f"全文来源: {stats['by_fulltext_source']}", flush=True)
    print(f"本体: 节点 {stats['nodes']} | 关系边 {stats['edges']} | "
          f"超边 {stats['hyperedges']} | 条件 {stats['conditions']} | "
          f"测量 {stats['measurements']}", flush=True)
    if stats["hyperedge_types"]:
        print(f"超边类型分布: {stats['hyperedge_types']}", flush=True)


def fetch(db: Path, args, settings: Settings, profile: dict) -> int:
    conn = connect(db)
    try:
        init_ontology(conn)
        queries = load_queries(args.domain, args.source, args.queries_file)
        if args.max_queries:
            queries = queries[:args.max_queries]
        done = set(meta_get(conn, "corpus_done_queries", []) or [])
        if args.redo_queries:
            done = set()
        reports = dict(meta_get(conn, "corpus_query_reports", {}) or {})
        gate_terms = [t for group in corpus_filter_terms(args.domain)
                      for t in group]
        gate_terms += [str(k) for k in
                       (packs.domain_data(args.domain).get("keywords") or [])]
        gate_terms += [profile.get("label") or "", args.topic or ""]
        api = ApiHub(source=args.source)
        print(f"[fetch] {len(queries)} 个检索式，已完成 {len(done)} 个，"
              f"每式最多 {args.per_query} 条，来源 {args.source}", flush=True)
        for i, query in enumerate(queries, 1):
            if query in done:
                continue
            if args.max_papers and _paper_count(conn) >= args.max_papers:
                print(f"[fetch] 已达 --max-papers={args.max_papers}，停止抓取",
                      flush=True)
                break
            t0 = time.time()
            try:
                out = ingest_search_results(
                    query, args.per_query, api=api, conn=conn, settings=settings,
                    fixed_queries=[query], topic_terms=gate_terms)
                reports[query] = {
                    "count": out.get("count"),
                    "relevance_gate": out.get("relevance_gate"),
                    "errors": len(out.get("errors") or []),
                }
                print(f"[fetch {i}/{len(queries)}] {query} -> "
                      f"入库 {out.get('count')} 篇 ({time.time() - t0:.0f}s) | "
                      f"库内累计 {_paper_count(conn)} 篇", flush=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("检索失败: %s", query)
                reports[query] = {"error": str(exc)}
                print(f"[fetch {i}/{len(queries)}] {query} -> 失败 {exc}",
                      flush=True)
            done.add(query)
            # 每个检索式后立即过一遍语料硬门（幂等、仅扫 papers 表），
            # 这样 --max-papers 统计的是"在范围内"的篇数
            marked = mark_out_of_scope(db, args.domain)
            meta_set(conn, "corpus_done_queries", sorted(done))
            meta_set(conn, "corpus_query_reports", reports)
            conn.commit()
            if marked["dropped"]:
                print(f"[filter] 累计剔除界外文献 {marked['dropped']} 篇", flush=True)
        marked = mark_out_of_scope(db, args.domain)
        if marked["dropped"]:
            print(f"[filter] 语料硬门剔除 {marked['dropped']} 篇界外文献 "
                  f"（示例 {marked['sample'][:5]}）", flush=True)
        return _paper_count(conn)
    finally:
        conn.close()


def fetch_skip_known(db: Path, args, settings: Settings, profile: dict) -> int:
    """追加抓取快路径：先检索、再把**已在库**的记录剔除，只下载新文献。

    与 ``fetch`` 的差别：不走 ``ingest_search_results`` 的内部检索，而是自己检索后
    复用其相关性硬门与处理管线（``apply_topic_relevance_gate`` + ``_process_records``），
    从而避免重复下载已在库论文的 PDF（追加检索式与原检索式高度重叠时会省很多时间）。
    """
    from research_agent.retrieval.node import (
        _process_records,
        apply_topic_relevance_gate,
    )

    conn = connect(db)
    try:
        init_ontology(conn)
        queries = load_queries(args.domain, args.source, args.queries_file)
        done = set(meta_get(conn, "corpus_done_queries", []) or [])
        reports = dict(meta_get(conn, "corpus_query_reports", {}) or {})
        known = {r["paper_key"] for r in
                 conn.execute("SELECT paper_key FROM papers")}
        gate_terms = [t for group in corpus_filter_terms(args.domain)
                      for t in group]
        gate_terms += [str(k) for k in
                       (packs.domain_data(args.domain).get("keywords") or [])]
        api = ApiHub(source=args.source)
        policy = str(getattr(settings, "relevance_gate_low_signal", "warn"))
        todo = [q for q in queries if q not in done]
        print(f"[fetch-skip] 待跑 {len(todo)} 个检索式（已完成 {len(done)}），"
              f"库内已有 {len(known)} 篇", flush=True)
        for i, query in enumerate(todo, 1):
            if args.max_papers and _paper_count(conn) >= args.max_papers:
                print(f"[fetch-skip] 已达 --max-papers={args.max_papers}，停止",
                      flush=True)
                break
            t0 = time.time()
            try:
                records = api.search(query, max_results=args.per_query)
                new = [r for r in records
                       if r.get("paper_key") and r["paper_key"] not in known]
                kept, dropped = apply_topic_relevance_gate(
                    new, gate_terms, on_low_signal=policy)
                out = _process_records(kept, retriever=None, api=api, conn=conn,
                                       query=query, queries=[query])
                known.update(out.get("paper_keys") or [])
                reports[query] = {"hits": len(records), "new": len(new),
                                  "kept": len(kept), "dropped": len(dropped),
                                  "ingested": out.get("count")}
                print(f"[fetch-skip {i}/{len(todo)}] {query} -> 命中 {len(records)} "
                      f"新增候选 {len(new)} 入库 {out.get('count')} "
                      f"({time.time() - t0:.0f}s) | 库内 {_paper_count(conn)} 篇",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("检索失败: %s", query)
                reports[query] = {"error": str(exc)}
                print(f"[fetch-skip {i}/{len(todo)}] {query} -> 失败 {exc}",
                      flush=True)
            done.add(query)
            meta_set(conn, "corpus_done_queries", sorted(done))
            meta_set(conn, "corpus_query_reports", reports)
            conn.commit()
            marked = mark_out_of_scope(db, args.domain)
            if marked["dropped"]:
                print(f"[filter] 累计剔除界外文献 {marked['dropped']} 篇", flush=True)
        return _paper_count(conn)
    finally:
        conn.close()


def _paper_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute(
        "SELECT COUNT(*) FROM papers WHERE status<>'out_of_scope'"
    ).fetchone()[0])


def _pending_keys(db: Path) -> list[str]:
    conn = connect(db)
    try:
        return [r["paper_key"] for r in conn.execute(
            """
            SELECT paper_key FROM papers
            WHERE length(COALESCE(clean_text, '')) > 0
              AND status<>'out_of_scope'
              AND paper_key NOT IN (SELECT paper_key FROM ontology_runs)
            ORDER BY paper_key
            """
        )]
    finally:
        conn.close()


def extract(db: Path, args, settings: Settings, profile: dict) -> dict:
    keys = _pending_keys(db)
    print(f"[extract] 待提取 {len(keys)} 篇，workers={args.workers}，"
          f"model={args.model}", flush=True)
    if not keys:
        return {"done": 0, "ok": 0, "error": 0}
    conns: list[sqlite3.Connection] = []
    for _ in range(max(1, args.workers)):
        c = connect(db)
        init_ontology(c)
        c.commit()
        conns.append(c)
    slices = [keys[i::len(conns)] for i in range(len(conns))]
    stats = {"done": 0, "ok": 0, "error": 0, "entities": 0, "hyperedges": 0,
             "failed_chunks": 0}
    lock = threading.Lock()
    start = time.time()
    total = len(keys)

    def _model():
        if args.model == "auto":
            return build_role_model("knowledge")
        name = ("deepseek-v4-pro" if args.model == "pro"
                else "deepseek-v4-flash")
        return build_chat_model(provider="deepseek", model_name=name,
                                temperature=0.2)

    def run_part(conn: sqlite3.Connection, part: list[str]) -> None:
        qnode = make_quality_node(api=None, model=None, conn=conn,
                                  settings=settings, offline=True)
        knew = make_knowledge_node(_model(), conn=conn, settings=settings,
                                   run_init=False)
        for key in part:
            t0 = time.time()
            try:
                qnode({"current_key": key, "meta_attempts": 0})
                out = knew({"current_key": key, "domain_profile": profile})
                rep = (out.get("extraction_report") or {})
                extracted = rep.get("extracted") or {}
                with lock:
                    stats["done"] += 1
                    stats["ok"] += 1
                    stats["entities"] += int(extracted.get("entities") or 0)
                    stats["hyperedges"] += int(extracted.get("hyperedges") or 0)
                    stats["failed_chunks"] += int(
                        extracted.get("failed_chunks") or 0)
                    done = stats["done"]
                    if done % 5 == 0 or done == total:
                        elapsed = time.time() - start
                        rate = done / max(1e-6, elapsed)
                        eta = (total - done) / max(1e-6, rate)
                        print(f"[extract {done}/{total}] "
                              f"{key} | ent={extracted.get('entities', 0)} "
                              f"hyper={extracted.get('hyperedges', 0)} "
                              f"conds={extracted.get('conditions', 0)} "
                              f"{time.time() - t0:.0f}s | 均 {rate:.2f}/s "
                              f"预计剩余 {eta / 60:.1f}min", flush=True)
            except Exception as exc:  # noqa: BLE001
                with lock:
                    stats["done"] += 1
                    stats["error"] += 1
                    print(f"[extract] error {key}: {exc}", flush=True)

    with ThreadPoolExecutor(max_workers=len(conns)) as pool:
        futures = [pool.submit(run_part, conn, part)
                   for conn, part in zip(conns, slices) if part]
        for fut in as_completed(futures):
            exc = fut.exception()
            if exc:
                print(f"[extract] worker error: {exc}", flush=True)
    for c in conns:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass
    wall = time.time() - start
    print(f"[extract] 完成 {stats['ok']}/{total}，错误 {stats['error']}，"
          f"墙钟 {wall / 60:.1f}min", flush=True)
    return stats


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="按领域包建语料库并提取知识")
    ap.add_argument("--db", required=True, help="目标数据库")
    ap.add_argument("--domain", default="code_based_crypto", help="领域包 kind")
    ap.add_argument("--topic", default="", help="主题描述（用于领域画像兜底）")
    ap.add_argument("--source", default="arxiv",
                    choices=["arxiv", "openalex", "semantic_scholar", "europepmc",
                             "pubmed", "both", "fulltext", "all", "ncpssd"])
    ap.add_argument("--queries-file", default=None, help="覆盖领域包的检索式文件")
    ap.add_argument("--per-query", type=int, default=15, help="每个检索式取多少条")
    ap.add_argument("--max-queries", type=int, default=0, help="最多执行几个检索式")
    ap.add_argument("--max-papers", type=int, default=0, help="库内论文达到多少篇停止抓取")
    ap.add_argument("--min-papers", type=int, default=300, help="目标篇数（仅提示）")
    ap.add_argument("--workers", type=int, default=6, help="提取并发")
    ap.add_argument("--model", choices=["auto", "pro", "flash"], default="flash")
    ap.add_argument("--max-chunks", type=int, default=3, help="每篇最多抽几个块")
    ap.add_argument("--chunk-chars", type=int, default=10000, help="单块字符上限")
    ap.add_argument("--refine", action="store_true", help="开启定向精修（更慢）")
    ap.add_argument("--fetch-only", action="store_true")
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--stats-only", action="store_true")
    ap.add_argument("--redo-queries", action="store_true", help="忽略已完成检索式")
    ap.add_argument("--skip-known", action="store_true",
                    help="追加抓取：先检索再剔除已在库记录，只下载新文献（更快）")
    args = ap.parse_args(argv)

    db = Path(args.db)
    db.parent.mkdir(parents=True, exist_ok=True)
    settings = Settings(db_path=db)
    settings.max_extract_chunks = max(1, args.max_chunks)
    settings.max_extract_chars = max(2000, args.chunk_chars)
    settings.knowledge_refine_enabled = bool(args.refine)
    profile = build_profile(args.domain, args.topic or args.domain)
    if not profile.get("candidate_entity_types"):
        print(f"[warn] 领域 {args.domain} 没有画像（检查 packs/domains/）", flush=True)

    if args.stats_only:
        print_stats(corpus_stats(db), "库统计")
        return 0
    if not args.extract_only:
        init = connect(db)
        try:
            init_ontology(init)
            init.commit()
        finally:
            init.close()
        total = (fetch_skip_known(db, args, settings, profile)
                 if args.skip_known else fetch(db, args, settings, profile))
        stats = corpus_stats(db)
        print_stats(stats, "抓取后统计")
        print(f"[target] 目标 {args.min_papers} 篇，当前 {total} 篇", flush=True)
        if args.fetch_only:
            return 0
    if not args.fetch_only:
        extract(db, args, settings, profile)
        print_stats(corpus_stats(db), "提取后统计")
    return 0


if __name__ == "__main__":
    sys.exit(main())
