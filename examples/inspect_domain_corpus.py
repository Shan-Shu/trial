# -*- coding: utf-8 -*-
"""领域语料/本体抽查：节点分布、超边形式、条件与度量是否按领域 schema 落库。

用法::

    python examples/inspect_domain_corpus.py --db data/hqc_code_based_crypto.db
    python examples/inspect_domain_corpus.py --db ... --sample 8 --paper arxiv:xxxx
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _pct(part: int, total: int) -> str:
    return f"{(100.0 * part / total):.1f}%" if total else "-"


def report(db: Path, sample: int = 5, paper: str | None = None) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM papers").fetchone()["c"]
        runs = conn.execute("SELECT COUNT(*) AS c FROM ontology_runs").fetchone()["c"]
        print(f"论文 {total} 篇 | 已提取 {runs} 篇 | 覆盖率 {_pct(runs, total)}")
        rows = conn.execute(
            "SELECT COALESCE(fulltext_source,'none') AS s, COUNT(*) AS c "
            "FROM papers GROUP BY s ORDER BY c DESC").fetchall()
        print("全文来源: " + ", ".join(f"{r['s']}={r['c']}" for r in rows))

        print("\n--- 节点类型分布（top 20）---")
        for r in conn.execute(
                "SELECT node_type, COUNT(*) AS c FROM ontology_nodes "
                "GROUP BY node_type ORDER BY c DESC LIMIT 20"):
            print(f"  {str(r['node_type']):26s} {r['c']}")

        print("\n--- 关系类型分布（top 15）---")
        for r in conn.execute(
                "SELECT relation_type, COUNT(*) AS c FROM ontology_edges "
                "GROUP BY relation_type ORDER BY c DESC LIMIT 15"):
            print(f"  {str(r['relation_type']):26s} {r['c']}")

        print("\n--- 超边类型分布 ---")
        for r in conn.execute(
                "SELECT hyperedge_type, COUNT(*) AS c FROM ontology_hyperedges "
                "GROUP BY hyperedge_type ORDER BY c DESC"):
            print(f"  {str(r['hyperedge_type']):26s} {r['c']}")

        print("\n--- 条件键分布（top 20）---")
        for r in conn.execute(
                "SELECT condition_key, COUNT(*) AS c "
                "FROM ontology_hyperedge_conditions GROUP BY condition_key "
                "ORDER BY c DESC LIMIT 20"):
            print(f"  {str(r['condition_key']):26s} {r['c']}")

        print("\n--- 度量指标分布（top 20）---")
        for r in conn.execute(
                "SELECT metric, COUNT(*) AS c FROM ontology_hyperedge_measurements "
                "GROUP BY metric ORDER BY c DESC LIMIT 20"):
            print(f"  {str(r['metric']):26s} {r['c']}")

        where, params = "", []
        if paper:
            where, params = "WHERE h.paper_key=?", [paper]
        print(f"\n--- 超边样例（{sample} 条）---")
        for h in conn.execute(
                f"SELECT h.hyperedge_id, h.hyperedge_type, h.label, h.paper_key, "
                f"h.confidence FROM ontology_hyperedges h {where} "
                f"ORDER BY h.confidence DESC LIMIT ?", params + [sample]):
            hid = h["hyperedge_id"]
            members = [r["name"] for r in conn.execute(
                "SELECT n.name FROM ontology_hyperedge_members m "
                "JOIN ontology_nodes n ON n.node_id=m.node_id "
                "WHERE m.hyperedge_id=? ORDER BY m.position", (hid,))]
            conds = [dict(r) for r in conn.execute(
                "SELECT condition_key, operator, value_text, unit "
                "FROM ontology_hyperedge_conditions WHERE hyperedge_id=? LIMIT 8",
                (hid,))]
            meas = [dict(r) for r in conn.execute(
                "SELECT metric, value_text, unit FROM ontology_hyperedge_measurements "
                "WHERE hyperedge_id=? LIMIT 8", (hid,))]
            ev = conn.execute(
                "SELECT span_text FROM ontology_hyperedge_evidence "
                "WHERE hyperedge_id=? LIMIT 1", (hid,)).fetchone()
            print(f"\n[{hid}] {h['hyperedge_type']} conf={h['confidence']:.2f} "
                  f"| {h['paper_key']}")
            print(f"  标签: {h['label']}")
            print(f"  成员: {members}")
            print(f"  条件: {json.dumps(conds, ensure_ascii=False)}")
            print(f"  度量: {json.dumps(meas, ensure_ascii=False)}")
            if ev:
                print(f"  证据: {str(ev['span_text'])[:160]}")
    finally:
        conn.close()


def list_papers(db: Path, limit: int = 0) -> None:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        sql = ("SELECT paper_key, title, venue, pub_year, fulltext_source, "
               "length(COALESCE(clean_text,'')) AS chars FROM papers "
               "ORDER BY pub_year DESC, paper_key")
        if limit:
            sql += f" LIMIT {int(limit)}"
        for r in conn.execute(sql):
            print(f"{str(r['fulltext_source']):9s} {str(r['pub_year']):>6s} "
                  f"{r['chars']:>7d}ch | {r['paper_key']:24s} | "
                  f"{str(r['title'])[:90]}")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="领域语料/本体抽查")
    ap.add_argument("--db", required=True)
    ap.add_argument("--sample", type=int, default=5)
    ap.add_argument("--paper", default=None, help="只看某篇论文的超边")
    ap.add_argument("--titles", type=int, default=0,
                    help="列出论文清单（可给出条数上限）")
    args = ap.parse_args(argv)
    if args.titles:
        list_papers(Path(args.db), args.titles)
        return 0
    report(Path(args.db), args.sample, args.paper)
    return 0


if __name__ == "__main__":
    sys.exit(main())
