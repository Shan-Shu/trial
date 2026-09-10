"""全局配置：路径、质量评估权重/阈值、学科前沿迭代速度等。

所有阈值都可经环境变量覆盖（见 Settings.from_env）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """集中管理全流水线的路径与超参数。"""

    # ---------- 路径 ----------
    project_root: Path = PROJECT_ROOT
    data_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data")
    pdf_dir: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "pdfs")
    db_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "research_agent.db")

    # ---------- 质量评估：Q = Q_WEIGHT_A*A + Q_WEIGHT_T*T ----------
    q_weight_a: float = 0.6
    q_weight_t: float = 0.4
    threshold_direct: float = 0.8   # Q >= 0.8 直接进入知识提取
    threshold_flag: float = 0.5     # 0.5 <= Q < 0.8 标记后进入知识提取；Q < 0.5 人工审核

    # ---------- 权威性 A 的子项权重 ----------
    a_venue_weight: float = 0.5     # 期刊/出版社分级（JCR/SCI 分区）
    a_hindex_weight: float = 0.3    # 作者 H 指数
    a_citation_weight: float = 0.2  # 被引用次数

    # ---------- 时效性 T ----------
    field_half_life: dict = field(default_factory=lambda: {
        "fast": 2.0,     # AI/ML/计算机 等高速迭代学科
        "medium": 4.0,   # 生物医学/化学/物理 等
        "slow": 8.0,     # 数学/基础理论 等
    })
    default_field_velocity: str = "medium"
    current_year: int = field(default_factory=lambda: date.today().year)

    # ---------- 元数据回补 ----------
    max_meta_attempts: int = 3      # 质量节点 → 检索节点回补的最大轮数
    required_meta_fields: tuple = ("authors", "affiliations", "publication", "doi")

    # ---------- 知识提取 ----------
    max_extract_chars: int = 8000   # 单次 LLM 抽取的文本窗口大小
    max_extract_chunks: int = 4     # 一篇论文最多抽取的文本块数（控制成本）
    q_flag_penalty: float = 0.85    # “标记后发送”的文献，质量权重折扣
    strong_edge_min_conf: float = 0.72  # 低于此阈值的强因果/调控边标记 candidate
    review_correctness_min: float = 0.85  # 审核：证据与引用正确性最低阈值

    # ---------- 质量控制：全局领域词典归并 ----------
    global_merge_enabled: bool = True
    global_merge_interval_nodes: int = 300

    # ---------- 知识提取·二次精修（v0.0.6） ----------
    knowledge_refine_enabled: bool = True      # 是否允许“低置信/泛化关系”二次精修
    refine_min_conf: float = 0.6               # 低于此置信度的实体/关系触发点名
    refine_max_items: int = 12                 # 单轮最多点名的问题数（防提示词膨胀）
    refine_max_attempts: int = 2               # 精修最多轮数（收敛信号提前结束）
    generic_fallback_types: tuple = ("related_to",)  # 视为“语义过宽”的兜底关系

    # ---------- 网络 ----------
    http_timeout: int = 30
    user_agent: str = "research-agent/0.1 (mailto:research@example.com)"

    # 本地已知期刊分区表（种子样例，可扩展；完整 JCR 数据需授权订阅）
    journal_quartiles_seed: dict = field(default_factory=lambda: {
        "nature": "Q1", "science": "Q1", "cell": "Q1",
        "the lancet": "Q1", "new england journal of medicine": "Q1",
        "nature machine intelligence": "Q1",
        "nature methods": "Q1",
        "ieee transactions on pattern analysis and machine intelligence": "Q1",
        "journal of machine learning research": "Q1",
        "proceedings of the national academy of sciences of the united states of america": "Q1",
        "nucleic acids research": "Q1",
        "bioinformatics": "Q1",
        "plos computational biology": "Q1",
        "physical review letters": "Q1",
        "physical review x": "Q1",
        "european journal of cancer": "Q1",
        "british journal of cancer": "Q1",
        "acm computing surveys": "Q1",
        "ieee transactions on knowledge and data engineering": "Q1",
        "artificial intelligence": "Q1",
        "nature communications": "Q1",
        "scientific reports": "Q2",
        "plos one": "Q2",
        "ieee access": "Q2",
        "frontiers in oncology": "Q2",
        "peerj": "Q2",
        "bmc bioinformatics": "Q2",
        "cancer letters": "Q1",
        "signal transduction and targeted therapy": "Q1",
        "cell reports": "Q1",
        "genome biology": "Q1",
        "briefings in bioinformatics": "Q1",
        "computers in biology and medicine": "Q2",
        "artificial intelligence in medicine": "Q2",
    })

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pdf_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls()
        s.q_weight_a = _env_float("RA_Q_WEIGHT_A", s.q_weight_a)
        s.q_weight_t = _env_float("RA_Q_WEIGHT_T", s.q_weight_t)
        s.threshold_direct = _env_float("RA_THRESHOLD_DIRECT", s.threshold_direct)
        s.threshold_flag = _env_float("RA_THRESHOLD_FLAG", s.threshold_flag)
        s.a_venue_weight = _env_float("RA_A_VENUE_W", s.a_venue_weight)
        s.a_hindex_weight = _env_float("RA_A_HINDEX_W", s.a_hindex_weight)
        s.a_citation_weight = _env_float("RA_A_CITATION_W", s.a_citation_weight)
        s.max_meta_attempts = int(os.getenv("RA_MAX_META_ATTEMPTS", s.max_meta_attempts))
        s.knowledge_refine_enabled = _env_bool(
            "RA_KNOWLEDGE_REFINE", s.knowledge_refine_enabled)
        s.refine_min_conf = _env_float("RA_REFINE_MIN_CONF", s.refine_min_conf)
        s.refine_max_items = int(os.getenv("RA_REFINE_MAX_ITEMS", s.refine_max_items))
        s.refine_max_attempts = int(
            os.getenv("RA_REFINE_MAX_ATTEMPTS", s.refine_max_attempts))
        s.review_correctness_min = _env_float(
            "RA_REVIEW_CORRECTNESS_MIN", s.review_correctness_min)
        s.global_merge_enabled = _env_bool("RA_GLOBAL_MERGE", s.global_merge_enabled)
        s.global_merge_interval_nodes = int(os.getenv(
            "RA_GLOBAL_MERGE_INTERVAL_NODES", s.global_merge_interval_nodes))
        db = os.getenv("RA_DB_PATH")
        if db:
            s.db_path = Path(db)
        return s


settings = Settings.from_env()
