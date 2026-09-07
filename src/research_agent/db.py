"""本地数据库：论文元数据、PDF BLOB、清洗文本、质量评估、处理日志。

采用 Python 标准库 sqlite3，零额外依赖。动态本体图存储见 ontology.store。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from research_agent.config import Settings, settings as default_settings


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
);

CREATE TABLE IF NOT EXISTS papers (
    paper_key        TEXT PRIMARY KEY,
    source           TEXT,
    title            TEXT,
    abstract         TEXT,
    doi              TEXT,
    venue            TEXT,
    venue_issn       TEXT,
    pmcid            TEXT,              -- PubMed Central ID（PubMed 源全文）
    fulltext_source  TEXT,              -- pdf / xml(EuropePMC) / abstract
    source_type      TEXT,            -- journal / repository / proceedings ...
    pub_year         INTEGER,
    pub_date         TEXT,
    publication_status TEXT,          -- 发表情况：如 "Published"/"Preprint"
    citation_count   INTEGER,
    avg_h_index      REAL,
    authors_meta     TEXT DEFAULT '[]',
    pdf_sha256       TEXT,
    pdf_size         INTEGER,
    pdf_blob         BLOB,
    clean_text       TEXT,
    clean_text_sha256 TEXT,
    status           TEXT DEFAULT 'raw',
    created_at       TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_papers_doi ON papers(doi);
CREATE INDEX IF NOT EXISTS idx_papers_year ON papers(pub_year);

CREATE TABLE IF NOT EXISTS quality_results (
    paper_key        TEXT PRIMARY KEY REFERENCES papers(paper_key) ON DELETE CASCADE,
    venue_factor     REAL,
    h_factor         REAL,
    citation_factor  REAL,
    authority        REAL,
    timeliness       REAL,
    quality          REAL,
    decision         TEXT,
    needs_review     INTEGER DEFAULT 0,
    meta_missing     TEXT DEFAULT '[]',
    rationale        TEXT,
    assessed_at      TEXT
);

CREATE TABLE IF NOT EXISTS processing_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key  TEXT,
    node       TEXT,
    event      TEXT,
    details    TEXT,
    ts         TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_paper ON processing_log(paper_key);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开数据库连接并确保 schema 存在。"""
    path = Path(db_path) if db_path else default_settings.db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=20000")
    conn.executescript(SCHEMA)
    # 老库迁移：补充新增列（若缺）
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(papers)")}
    for col, decl in (("pmcid", "TEXT"), ("fulltext_source", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {decl}")
    conn.commit()
    return conn


def meta_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
    return json.loads(row["v"]) if row else default


def meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(k, v) VALUES(?, ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (key, json.dumps(value, ensure_ascii=False)),
    )


def meta_bump(conn: sqlite3.Connection, key: str, delta: int = 1) -> int:
    cur = int(meta_get(conn, key, 0)) + delta
    meta_set(conn, key, cur)
    return cur


def log_event(conn: sqlite3.Connection, node: str, event: str,
              paper_key: str | None = None, details: Any = None) -> None:
    conn.execute(
        "INSERT INTO processing_log(paper_key, node, event, details, ts) VALUES(?,?,?,?,?)",
        (paper_key, node, event,
         json.dumps(details, ensure_ascii=False, default=str) if details is not None else None,
         utcnow()),
    )
    conn.commit()


def upsert_paper(conn: sqlite3.Connection, rec: dict[str, Any]) -> str:
    """写入/更新论文主记录，返回 paper_key。rec 中 authors 序列化进 authors_meta。"""
    key = rec["paper_key"]
    now = utcnow()
    authors = rec.get("authors") or []
    existing = conn.execute("SELECT created_at FROM papers WHERE paper_key=?", (key,)).fetchone()
    created = existing["created_at"] if existing else now
    conn.execute(
        """
        INSERT INTO papers(
            paper_key, source, title, abstract, doi, venue, venue_issn, source_type,
            pmcid, fulltext_source, pub_year, pub_date, publication_status,
            citation_count, avg_h_index, authors_meta, pdf_sha256, pdf_size,
            pdf_blob, clean_text, clean_text_sha256, status, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(paper_key) DO UPDATE SET
            source=excluded.source, title=excluded.title, abstract=excluded.abstract,
            doi=excluded.doi, venue=excluded.venue, venue_issn=excluded.venue_issn,
            pmcid=excluded.pmcid, fulltext_source=excluded.fulltext_source,
            source_type=excluded.source_type, pub_year=excluded.pub_year,
            pub_date=excluded.pub_date, publication_status=excluded.publication_status,
            citation_count=excluded.citation_count, avg_h_index=excluded.avg_h_index,
            authors_meta=excluded.authors_meta, pdf_sha256=excluded.pdf_sha256,
            pdf_size=excluded.pdf_size, pdf_blob=excluded.pdf_blob,
            clean_text=excluded.clean_text,
            clean_text_sha256=excluded.clean_text_sha256,
            status=excluded.status, updated_at=excluded.updated_at
        """,
        (
            key, rec.get("source"), rec.get("title"), rec.get("abstract"),
            rec.get("doi"), rec.get("venue"), rec.get("venue_issn"),
            rec.get("source_type"), rec.get("pmcid"), rec.get("fulltext_source"),
            rec.get("pub_year"), rec.get("pub_date"),
            rec.get("publication_status"), rec.get("citation_count"),
            rec.get("avg_h_index"), json.dumps(authors, ensure_ascii=False),
            rec.get("pdf_sha256"), rec.get("pdf_size"), rec.get("pdf_blob"),
            rec.get("clean_text"), rec.get("clean_text_sha256"),
            rec.get("status", "ingested"), created, now,
        ),
    )
    conn.commit()
    return key


def get_paper(conn: sqlite3.Connection, paper_key: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM papers WHERE paper_key=?", (paper_key,)).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["authors"] = json.loads(d.pop("authors_meta") or "[]")
    except json.JSONDecodeError:
        d["authors"] = []
    return d


def iter_papers(conn: sqlite3.Connection, status: str | None = None) -> Iterable[dict]:
    if status:
        rows = conn.execute("SELECT paper_key FROM papers WHERE status=?", (status,))
    else:
        rows = conn.execute("SELECT paper_key FROM papers")
    for r in rows:
        rec = get_paper(conn, r["paper_key"])
        if rec:
            yield rec


def save_quality_result(conn: sqlite3.Connection, result: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO quality_results(
            paper_key, venue_factor, h_factor, citation_factor, authority,
            timeliness, quality, decision, needs_review, meta_missing, rationale, assessed_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(paper_key) DO UPDATE SET
            venue_factor=excluded.venue_factor, h_factor=excluded.h_factor,
            citation_factor=excluded.citation_factor, authority=excluded.authority,
            timeliness=excluded.timeliness, quality=excluded.quality,
            decision=excluded.decision, needs_review=excluded.needs_review,
            meta_missing=excluded.meta_missing, rationale=excluded.rationale,
            assessed_at=excluded.assessed_at
        """,
        (
            result["paper_key"], result.get("venue_factor"), result.get("h_factor"),
            result.get("citation_factor"), result.get("authority"),
            result.get("timeliness"), result.get("quality"), result.get("decision"),
            int(bool(result.get("needs_review"))),
            json.dumps(result.get("meta_missing", []), ensure_ascii=False),
            result.get("rationale"), utcnow(),
        ),
    )
    conn.commit()


def get_quality_result(conn: sqlite3.Connection, paper_key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM quality_results WHERE paper_key=?", (paper_key,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["meta_missing"] = json.loads(d.get("meta_missing") or "[]")
    except json.JSONDecodeError:
        d["meta_missing"] = []
    return d
