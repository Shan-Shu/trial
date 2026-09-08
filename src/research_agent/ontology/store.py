"""动态本体图存储（SQLite）。

设计要点：
1. **动态 schema**：ontology_type_registry 同时存放“节点类型”与“关系类型”，
   知识提取发现新类型时自动注册（origin='dynamic'），本体版本号随之递增——
   这是“动态本体”演化的核心。
2. **合并更新**：节点以 (node_type, normalized_name) 唯一；重复出现时
   合并别名/属性/来源证据，置信度取 max（新证据不降低旧结论，只增补）。
3. **溯源**：每个节点/边记录 provenance（来源论文 + 证据句子），可审计。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from research_agent.db import meta_bump, meta_get, meta_set, utcnow


SEED_NODE_TYPES = [
    ("Method", "方法/技术"),
    ("Dataset", "数据集"),
    ("Metric", "指标/评测标准"),
    ("Task", "任务"),
    ("Concept", "概念"),
    ("Tool", "工具/软件/模型"),
    ("Person", "人物/作者"),
    ("Organization", "机构/组织"),
    ("Material", "材料/样本"),
    ("Disease", "疾病"),
    ("Drug", "药物"),
    ("Gene", "基因/蛋白"),
    ("Event", "事件"),
]

SEED_RELATION_TYPES = [
    ("uses", "使用"),
    ("evaluates", "评估"),
    ("compares", "比较"),
    ("part_of", "属于/组成"),
    ("improves_upon", "改进自"),
    ("based_on", "基于"),
    ("promotes", "促进/增强/加速"),
    ("regulates", "调控"),
    ("activates", "激活"),
    ("releases", "释放/缓释"),
    ("differentiates_into", "分化为"),
    ("correlates_with", "与…相关/随…变化(非因果)"),
    ("enables", "使能/实现/支持(应用/功能)"),
    ("complicates", "并发/加重(并发症)"),
    ("risk_factor_for", "是…的风险因素"),
    ("results_in", "导致…结果(过程→结果)"),
    ("is_a", "是…的一种(类型层级)"),
    ("inhibits", "抑制"),
    ("made_of", "由…制成/组成"),
    ("produced_by", "由…产生/合成"),
    ("related_to", "相关/关联"),
    ("regulates", "调控"),
    ("cites", "引用"),
    ("published_in", "发表于"),
    ("authored_by", "作者"),
    ("developed_by", "开发自"),
    ("causes", "导致"),
    ("treats", "治疗"),
    ("targets", "作用于"),
    ("has_property", "具有属性"),
]

RELATION_SYNONYMS = {
    "utilize": "uses", "utilizes": "uses", "employ": "uses", "employs": "uses",
    "apply": "uses", "applies": "uses", "applied": "uses", "used in": "uses",
    "assess": "evaluates", "assessed": "evaluates", "benchmark": "evaluates",
    "benchmarked": "evaluates", "test on": "evaluates", "tested on": "evaluates",
    "validate": "evaluates", "validated": "evaluates",
    "consists of": "made_of", "composed of": "made_of", "comprised of": "made_of",
    "fabricated from": "made_of", "made from": "made_of",
    "lead to": "causes", "leads to": "causes", "contributes to": "causes",
    "trigger": "causes", "triggered": "causes",
    "promote": "promotes", "promotes": "promotes", "enhance": "promotes",
    "enhances": "promotes", "facilitates": "promotes", "accelerates": "promotes",
    "accelerate": "promotes", "boost": "promotes", "boosted": "promotes",
    "induce": "promotes", "induces": "promotes", "induced": "promotes",
    "upregulate": "regulates", "upregulates": "regulates",
    "activate": "activates", "activates": "activates",
    "release": "releases", "releases": "releases", "elute": "releases",
    "sustained release": "releases",
    "differentiate into": "differentiates_into",
    "differentiated into": "differentiates_into",
    "differentiation into": "differentiates_into",
    "correlate": "correlates_with", "correlates": "correlates_with",
    "correlated with": "correlates_with",
    "track": "correlates_with", "tracks": "correlates_with",
    "enable": "enables", "enables": "enables", "allowed": "enables",
    "makes possible": "enables",
    "complicate": "complicates", "complicates": "complicates",
    "complication of": "complicates",
    "risk factor for": "risk_factor_for", "predispose to": "risk_factor_for",
    "predisposes to": "risk_factor_for",
    "result in": "results_in", "results in": "results_in",
    "resulting in": "results_in",
    "is a": "is_a", "is an": "is_a", "a kind of": "is_a",
    "type of": "is_a", "subclass of": "is_a",
    "suppress": "inhibits", "suppresses": "inhibits", "downregulates": "inhibits",
    "exhibit": "has_property", "exhibits": "has_property", "possesses": "has_property",
    "shows": "has_property", "display": "has_property",
    "derived from": "based_on", "originates from": "based_on",
    "outperform": "improves_upon", "outperforms": "improves_upon",
    "better than": "improves_upon", "superior to": "improves_upon",
    "act on": "targets", "acts on": "targets", "bind": "targets", "binds": "targets",
    "interacts with": "targets",
    "associated with": "related_to", "relates to": "related_to",
    "involved in": "related_to",
    "produced by": "produced_by", "synthesized by": "produced_by",
    "secreted by": "produced_by", "generate": "produced_by",
    "compare with": "compares", "compared with": "compares",
    "compare to": "compares", "compared to": "compares",
    "versus": "compares",
    "cure": "treats", "treat": "treats", "treated": "treats",
    "part of": "part_of", "belong to": "part_of",
    "cite": "cites", "reference": "cites", "references": "cites",
    "publish in": "published_in", "appear in": "published_in",
    "author by": "authored_by", "written by": "authored_by",
    "develop": "developed_by", "create": "developed_by", "creates": "developed_by",
    "designed by": "developed_by",
}


def canonical_relation_type(relation_type: str) -> str:
    """把同义/动词化变体归一到受控词表词；无法归一则保留原词。"""
    key = re.sub(r"\s+", " ", str(relation_type or "").strip().lower())
    return RELATION_SYNONYMS.get(key, relation_type)


ONTOLOGY_SCHEMA = """
CREATE TABLE IF NOT EXISTS ontology_type_registry (
    type_key    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,          -- 'node' | 'relation'
    label       TEXT,
    origin      TEXT DEFAULT 'seed',    -- 'seed' | 'dynamic'
    created_at  TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS ontology_nodes (
    node_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    node_type      TEXT NOT NULL,
    name           TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    aliases        TEXT DEFAULT '[]',
    attributes     TEXT DEFAULT '{}',
    confidence     REAL DEFAULT 0.5,
    first_seen_at  TEXT,
    last_seen_at   TEXT,
    provenance     TEXT DEFAULT '[]',
    UNIQUE(node_type, normalized_name)
);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON ontology_nodes(node_type);

CREATE TABLE IF NOT EXISTS ontology_edges (
    edge_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_type TEXT NOT NULL,
    source_node   INTEGER NOT NULL,
    target_node   INTEGER NOT NULL,
    attributes    TEXT DEFAULT '{}',
    confidence    REAL DEFAULT 0.5,
    created_at    TEXT,
    provenance    TEXT DEFAULT '[]',
    UNIQUE(relation_type, source_node, target_node)
);
CREATE INDEX IF NOT EXISTS idx_edges_type ON ontology_edges(relation_type);

CREATE TABLE IF NOT EXISTS ontology_runs (
    run_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key        TEXT,
    counts           TEXT,
    new_types        TEXT,
    ontology_version INTEGER,
    ran_at           TEXT
);

CREATE TABLE IF NOT EXISTS event_assertions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_key       TEXT,
    event_type      TEXT,
    trigger         TEXT,
    participants    TEXT DEFAULT '[]',
    entity_refs     TEXT DEFAULT '[]',
    time_text       TEXT,
    attributes      TEXT DEFAULT '{}',
    confidence      REAL DEFAULT 0.5,
    provenance      TEXT DEFAULT '[]',
    created_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_event_paper ON event_assertions(paper_key);

CREATE TABLE IF NOT EXISTS material_registry (
    local_id        TEXT PRIMARY KEY,
    preferred_name  TEXT,
    synonyms        TEXT DEFAULT '[]',
    composition     TEXT DEFAULT '{}',
    term_status     TEXT DEFAULT 'local_uncurated',
    curated_by      TEXT,
    created_at      TEXT,
    updated_at      TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS merge_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    from_ids        TEXT DEFAULT '[]',
    to_id           INTEGER,
    rule_level      TEXT,
    reason          TEXT,
    operator        TEXT,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS merge_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_ids        TEXT DEFAULT '[]',
    reason          TEXT,
    status          TEXT DEFAULT 'open',
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS direction_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    edge_id         INTEGER,
    relation_type   TEXT,
    source_node     INTEGER,
    target_node     INTEGER,
    suggestion      TEXT,
    status          TEXT DEFAULT 'open',
    created_at      TEXT
);
"""


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def _norm(name: str) -> str:
    """规范化名称：小写、去空白与常见分隔符，用于合并判定。"""
    name = re.sub(r"[\s_\-/\\.,;:'\"()\[\]{}]+", " ", str(name)).strip().lower()
    return re.sub(r"\s+", " ", name)


def _find_node_by_alias(conn: sqlite3.Connection, node_type: str,
                        names: list[str]) -> sqlite3.Row | None:
    """在同类型节点中按 name/normalized_name/别名查找匹配（用于去重合并）。"""
    norms = {_norm(n) for n in names if n and _norm(n)}
    if not norms:
        return None
    ph = ",".join("?" * len(norms))
    row = conn.execute(
        f"SELECT node_id, node_type, name, normalized_name, confidence, aliases, "
        f"attributes, provenance FROM ontology_nodes "
        f"WHERE node_type=? AND normalized_name IN ({ph}) LIMIT 1",
        [node_type, *norms],
    ).fetchone()
    if row:
        return row
    # 再按既有节点的 aliases 内容匹配
    rows = conn.execute(
        "SELECT node_id, node_type, name, normalized_name, confidence, aliases, "
        "attributes, provenance FROM ontology_nodes WHERE node_type=?",
        (node_type,),
    ).fetchall()
    for r in rows:
        try:
            alias_list = json.loads(r["aliases"] or "[]")
        except json.JSONDecodeError:
            alias_list = []
        alias_norms = {_norm(x) for x in alias_list}
        if norms & alias_norms:
            return r
    return None


def init_ontology(conn: sqlite3.Connection) -> None:
    conn.executescript(ONTOLOGY_SCHEMA)
    _ensure_column(conn, "ontology_nodes", "identity_key", "TEXT")
    _ensure_column(conn, "ontology_nodes", "external_source", "TEXT")
    _ensure_column(conn, "ontology_nodes", "external_id", "TEXT")
    _ensure_column(conn, "ontology_nodes", "term_status",
                   "TEXT DEFAULT 'local_uncurated'")
    _ensure_column(conn, "ontology_nodes", "scope_tag", "TEXT")
    _ensure_column(conn, "ontology_nodes", "evidence_tier",
                   "TEXT DEFAULT 'unclassified'")
    _ensure_column(conn, "ontology_edges", "evidence_tier",
                   "TEXT DEFAULT 'unclassified'")
    for key, label in SEED_NODE_TYPES:
        _ensure_type_row(conn, key, "node", label, "seed")
    for key, label in SEED_RELATION_TYPES:
        _ensure_type_row(conn, key, "relation", label, "seed")
    if meta_get(conn, "ontology_schema_version") is None:
        meta_set(conn, "ontology_schema_version", 1)
    conn.commit()


def _ensure_type_row(conn: sqlite3.Connection, type_key: str, kind: str,
                     label: str | None, origin: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ontology_type_registry WHERE type_key=?", (type_key,)
    ).fetchone()
    if row:
        return False
    conn.execute(
        "INSERT INTO ontology_type_registry(type_key, kind, label, origin, created_at) "
        "VALUES(?,?,?,?,?)",
        (type_key, kind, label, origin, utcnow()),
    )
    return True


def ensure_node_type(conn: sqlite3.Connection, type_key: str,
                     label: str | None = None) -> bool:
    """注册节点类型（若不存在）。返回是否为新类型（动态演化触发）。"""
    is_new = _ensure_type_row(conn, type_key, "node", label, "dynamic")
    if is_new:
        meta_bump(conn, "ontology_schema_version")
        conn.commit()
    return is_new


def ensure_relation_type(conn: sqlite3.Connection, type_key: str,
                         label: str | None = None) -> bool:
    """注册关系类型（若不存在）。返回是否为新类型。"""
    is_new = _ensure_type_row(conn, type_key, "relation", label, "dynamic")
    if is_new:
        meta_bump(conn, "ontology_schema_version")
        conn.commit()
    return is_new


def _merge_aliases(old: list, new: list) -> list:
    out = list(old)
    for a in new or []:
        if a and a not in out:
            out.append(a)
    return out


def _merge_attributes(old: dict, new: dict, provenance_key: str) -> dict:
    """属性合并：old/new 均为 {attr: value}；同属性取新值并记录来源。"""
    merged = dict(old or {})
    for k, v in (new or {}).items():
        entry = {"value": v, "source": provenance_key}
        prev = merged.get(k)
        if isinstance(prev, list) and prev and "value" in prev[-1]:
            prev.append(entry)
        else:
            merged[k] = [entry] if prev is not None else entry
        # 若此前为单值 dict 形态，则升级为列表
        if isinstance(merged[k], dict) and "source" in merged[k]:
            merged[k] = [merged[k]]
    return merged


def upsert_node(conn: sqlite3.Connection, *, node_type: str, name: str,
                confidence: float, aliases: list[str] | None = None,
                attributes: dict | None = None,
                provenance: list[dict] | None = None) -> tuple[int, bool]:
    """插入或合并节点，返回 (node_id, is_new)。"""
    norm = _norm(name)
    now = utcnow()
    ensure_node_type(conn, node_type)
    row = conn.execute(
        "SELECT node_id, name, normalized_name, confidence, aliases, attributes, "
        "provenance FROM ontology_nodes "
        "WHERE node_type=? AND normalized_name=?",
        (node_type, norm),
    ).fetchone()
    if not row:
        # 按别名去重：论文换了一种写法（或给出了既有规范名的别名）也并入同一节点
        row = _find_node_by_alias(conn, node_type, [name] + list(aliases or []))
    if not row:
        cur = conn.execute(
            "INSERT INTO ontology_nodes(node_type, name, normalized_name, aliases, "
            "attributes, confidence, first_seen_at, last_seen_at, provenance) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (node_type, name, norm,
             json.dumps(aliases or [], ensure_ascii=False),
             json.dumps(attributes or {}, ensure_ascii=False),
             max(0.0, min(1.0, confidence)), now, now,
             json.dumps(provenance or [], ensure_ascii=False)),
        )
        return int(cur.lastrowid), True

    new_conf = max(float(row["confidence"]), float(confidence))
    old_aliases = json.loads(row["aliases"] or "[]")
    old_attr = json.loads(row["attributes"] or "{}")
    old_prov = json.loads(row["provenance"] or "[]")
    extra_aliases = list(aliases or [])
    if _norm(row["name"]) != norm and name not in extra_aliases:
        extra_aliases.append(name)   # 该写法成为新别名，记录在既有规范节点下
    merged_attr = _merge_attributes(old_attr, attributes or {}, provenance_key=name)
    merged_prov = _dedup_provenance(old_prov + (provenance or []))
    conn.execute(
        "UPDATE ontology_nodes SET aliases=?, attributes=?, confidence=?, last_seen_at=?, "
        "provenance=? WHERE node_id=?",
        (
            json.dumps(_merge_aliases(old_aliases, extra_aliases), ensure_ascii=False),
            json.dumps(merged_attr, ensure_ascii=False),
            new_conf, now,
            json.dumps(merged_prov, ensure_ascii=False),
            row["node_id"],
        ),
    )
    return int(row["node_id"]), False


def _dedup_provenance(prov: list) -> list:
    seen: set = set()
    out = []
    for p in prov:
        sig = json.dumps(p, ensure_ascii=False, sort_keys=True)
        if sig not in seen:
            seen.add(sig)
            out.append(p)
    return out


def upsert_edge(conn: sqlite3.Connection, *, relation_type: str,
                src_id: int, tgt_id: int, confidence: float,
                attributes: dict | None = None,
                provenance: list[dict] | None = None,
                evidence_tier: str | None = None) -> tuple[int, bool]:
    """插入或合并关系边，返回 (edge_id, is_new)。"""
    relation_type = canonical_relation_type(relation_type)
    ensure_relation_type(conn, relation_type)
    now = utcnow()
    row = conn.execute(
        "SELECT edge_id, confidence, provenance, evidence_tier FROM ontology_edges "
        "WHERE relation_type=? AND source_node=? AND target_node=?",
        (relation_type, src_id, tgt_id),
    ).fetchone()
    if not row:
        cur = conn.execute(
            "INSERT INTO ontology_edges(relation_type, source_node, target_node, "
            "attributes, confidence, created_at, provenance, evidence_tier) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (relation_type, src_id, tgt_id,
             json.dumps(attributes or {}, ensure_ascii=False),
             max(0.0, min(1.0, confidence)), now,
             json.dumps(provenance or [], ensure_ascii=False),
             evidence_tier),
        )
        return int(cur.lastrowid), True
    new_conf = max(float(row["confidence"]), float(confidence))
    old_prov = json.loads(row["provenance"] or "[]")
    merged_prov = _dedup_provenance(old_prov + (provenance or []))
    tier = evidence_tier or row["evidence_tier"]
    conn.execute(
        "UPDATE ontology_edges SET confidence=?, provenance=?, evidence_tier=? "
        "WHERE edge_id=?",
        (new_conf, json.dumps(merged_prov, ensure_ascii=False), tier, row["edge_id"]),
    )
    return int(row["edge_id"]), False


def get_node_id(conn: sqlite3.Connection, node_type: str, name: str) -> int | None:
    row = conn.execute(
        "SELECT node_id FROM ontology_nodes WHERE node_type=? AND normalized_name=?",
        (node_type, _norm(name)),
    ).fetchone()
    return int(row["node_id"]) if row else None


def record_ontology_run(conn: sqlite3.Connection, paper_key: str,
                        counts: dict, new_types: list[str]) -> int:
    version = meta_bump(conn, "ontology_instance_version")
    cur = conn.execute(
        "INSERT INTO ontology_runs(paper_key, counts, new_types, ontology_version, ran_at) "
        "VALUES(?,?,?,?,?)",
        (
            paper_key,
            json.dumps(counts, ensure_ascii=False),
            json.dumps(new_types, ensure_ascii=False),
            version, utcnow(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


STRONG_RELATIONS = {
    "promotes", "regulates", "activates", "inhibits", "causes",
    "treats", "targets", "differentiates_into", "releases", "results_in",
}


def add_event_assertion(conn: sqlite3.Connection, *, paper_key: str,
                        event_type: str, trigger: str,
                        participants: list[str], entity_refs: list[int],
                        time_text: str | None = None,
                        attributes: dict | None = None,
                        confidence: float = 0.5,
                        provenance: list[dict] | None = None) -> int:
    """把事件写入旁路表（不再生成事件节点/星型 involves 边）。"""
    cur = conn.execute(
        "INSERT INTO event_assertions(paper_key, event_type, trigger, participants, "
        "entity_refs, time_text, attributes, confidence, provenance, created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            paper_key, event_type, trigger,
            json.dumps(participants, ensure_ascii=False),
            json.dumps(entity_refs, ensure_ascii=False),
            time_text,
            json.dumps(attributes or {}, ensure_ascii=False),
            max(0.0, min(1.0, confidence)),
            json.dumps(provenance or [], ensure_ascii=False),
            utcnow(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def register_material(conn: sqlite3.Connection, preferred_name: str, *,
                      synonyms: list[str] | None = None,
                      composition: dict | None = None,
                      term_status: str = "local_uncurated",
                      curated_by: str | None = None,
                      notes: str | None = None) -> str:
    """本地材料登记（lcmat 命名空间）。返回 local_id。"""
    slug = re.sub(r"[^a-z0-9]+", "-", preferred_name.lower()).strip("-")[:60]
    local_id = f"lcmat:{slug or 'unnamed'}"
    now = utcnow()
    conn.execute(
        "INSERT INTO material_registry(local_id, preferred_name, synonyms, composition, "
        "term_status, curated_by, created_at, updated_at, notes) "
        "VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(local_id) DO UPDATE SET preferred_name=excluded.preferred_name, "
        "synonyms=excluded.synonyms, composition=excluded.composition, "
        "term_status=excluded.term_status, updated_at=excluded.updated_at, "
        "notes=excluded.notes",
        (
            local_id, preferred_name,
            json.dumps(synonyms or [], ensure_ascii=False),
            json.dumps(composition or {}, ensure_ascii=False),
            term_status, curated_by, now, now, notes,
        ),
    )
    conn.commit()
    return local_id


def queue_merge_candidate(conn: sqlite3.Connection, node_ids: list[int],
                          reason: str) -> int:
    cur = conn.execute(
        "INSERT INTO merge_candidates(node_ids, reason, status, created_at) "
        "VALUES(?,?,?,?)",
        (json.dumps(node_ids), reason, "open", utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def log_merge(conn: sqlite3.Connection, from_ids: list[int], to_id: int, *,
              rule_level: str, reason: str, operator: str = "auto") -> int:
    cur = conn.execute(
        "INSERT INTO merge_journal(from_ids, to_id, rule_level, reason, operator, "
        "created_at) VALUES(?,?,?,?,?,?)",
        (json.dumps(from_ids), to_id, rule_level, reason, operator, utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def queue_direction_flag(conn: sqlite3.Connection, *, edge_id: int,
                         relation_type: str, source_node: int, target_node: int,
                         suggestion: str) -> int:
    cur = conn.execute(
        "INSERT INTO direction_queue(edge_id, relation_type, source_node, target_node, "
        "suggestion, status, created_at) VALUES(?,?,?,?,?,?,?)",
        (edge_id, relation_type, source_node, target_node, suggestion, "open", utcnow()),
    )
    conn.commit()
    return int(cur.lastrowid)


def graph_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    n_nodes = conn.execute("SELECT COUNT(*) AS c FROM ontology_nodes").fetchone()["c"]
    n_edges = conn.execute("SELECT COUNT(*) AS c FROM ontology_edges").fetchone()["c"]
    n_types = conn.execute("SELECT COUNT(*) AS c FROM ontology_type_registry").fetchone()["c"]
    node_by_type = {
        r["node_type"]: r["c"]
        for r in conn.execute(
            "SELECT node_type, COUNT(*) AS c FROM ontology_nodes GROUP BY node_type"
        )
    }
    edge_by_type = {
        r["relation_type"]: r["c"]
        for r in conn.execute(
            "SELECT relation_type, COUNT(*) AS c FROM ontology_edges GROUP BY relation_type"
        )
    }
    return {
        "nodes": n_nodes,
        "edges": n_edges,
        "types": n_types,
        "node_by_type": node_by_type,
        "edge_by_type": edge_by_type,
        "schema_version": meta_get(conn, "ontology_schema_version", 1),
        "instance_version": meta_get(conn, "ontology_instance_version", 0),
    }
