"""领域画像与候选领域 schema。

工作规划节点只输出“任务/领域/维度”，不再把生物医药或化学的领域模板
硬编码到检索/抽取节点中。领域实体类型与关系类型首轮可作为候选，之后
应由人工/审计冻结为领域 schema。
"""
from __future__ import annotations

from typing import Any


DOMAIN_PROFILES: dict[str, dict[str, Any]] = {
    "chemistry": {
        "label": "化学合成/催化",
        "dimensions": [
            "反应类型",
            "催化剂与试剂",
            "底物范围",
            "区域选择性",
            "立体选择性",
            "反应机理",
            "产率与反应条件",
            "官能团兼容性",
            "放大与绿色化学",
        ],
        "candidate_entity_types": [
            "Reaction", "Substrate", "Reagent", "Catalyst",
            "Product", "Condition", "Yield", "Chemical",
            "Ligand", "Solvent", "Method",
        ],
        "candidate_relation_types": [
            "catalyzed_by", "uses_as_reactant", "uses", "affords",
            "produces", "requires", "occurs_under", "gives_yield",
            "tolerates", "is_a", "part_of", "improves_upon", "enables",
        ],
    },
    "biomedicine": {
        "label": "生物医学/材料",
        "dimensions": [
            "分子与细胞机制",
            "生物相容性",
            "免疫反应",
            "适应症",
            "安全性与毒性",
            "药代/代谢",
            "临床转化",
        ],
        "candidate_entity_types": [
            "Disease", "Drug", "CellType", "BiologicalProcess",
            "Target", "Protein", "Gene", "Material", "Method",
            "AnimalModel", "ClinicalOutcome",
        ],
        "candidate_relation_types": [
            "promotes", "inhibits", "regulates", "treats",
            "targets", "enables", "causes", "correlates_with",
            "is_a", "part_of", "improves_upon",
        ],
    },
    "materials": {
        "label": "材料/工程",
        "dimensions": [
            "材料组成",
            "制备工艺",
            "结构与形貌",
            "力学性能",
            "功能性能",
            "稳定性",
            "应用场景",
        ],
        "candidate_entity_types": [
            "Material", "Device", "Method", "Process",
            "Property", "Metric", "Application", "Chemical",
        ],
        "candidate_relation_types": [
            "made_of", "part_of", "uses", "enables",
            "has_property", "improves_upon", "is_a", "evaluates",
        ],
    },
    "general": {
        "label": "通用科研",
        "dimensions": [
            "核心主题",
            "方法与技术",
            "应用场景",
            "机理与原理",
            "性能与评价",
            "变体与拓展",
            "开放问题",
        ],
        "candidate_entity_types": [
            "Concept", "Method", "Task", "Dataset", "Metric",
            "Theory", "Tool", "Application",
        ],
        "candidate_relation_types": [
            "uses", "evaluates", "based_on", "part_of",
            "improves_upon", "enables", "is_a", "related_to",
        ],
    },
}


DOMAIN_HINTS = [
    (
        ("炔酰胺", "ynamide", "环化", "催化", "底物", "产率", "选择性",
         "反应", "合成", "催化剂", "heterocycle", "annulation"),
        "chemistry",
    ),
    (
        ("疾病", "药物", "细胞", "适应症", "临床", "患者", "免疫",
         "生物相容", "组织修复", "毒性", "clinical", "disease", "drug"),
        "biomedicine",
    ),
    (
        ("材料", "支架", "涂层", "力学", "生物材料", "工艺", "composite",
         "scaffold", "material"),
        "materials",
    ),
]


def infer_domain_kind(domain: str = "", request: str = "") -> str:
    text = f"{domain} {request}".lower()
    for keywords, kind in DOMAIN_HINTS:
        if any(k.lower() in text for k in keywords):
            return kind
    return "general"


def normalize_domain_profile(data: dict[str, Any] | None,
                             domain: str = "",
                             request: str = "") -> dict[str, Any]:
    """根据规划节点输入生成稳定领域画像；缺失字段用默认画像补齐。"""
    raw = data or {}
    kind = (str(raw.get("domain_kind") or "").strip().lower()
            or infer_domain_kind(domain, request))
    if kind not in DOMAIN_PROFILES:
        kind = "general"
    profile = DOMAIN_PROFILES[kind]
    dims = [str(x).strip() for x in raw.get("dimensions") or [] if str(x).strip()]
    entity_types = [
        str(x).strip() for x in raw.get("candidate_entity_types") or []
        if str(x).strip()
    ]
    relation_types = [
        str(x).strip() for x in raw.get("candidate_relation_types") or []
        if str(x).strip()
    ]
    return {
        "domain_kind": kind,
        "label": profile["label"],
        "dimensions": dims or list(profile["dimensions"]),
        "candidate_entity_types": entity_types or list(profile["candidate_entity_types"]),
        "candidate_relation_types": relation_types or list(
            profile["candidate_relation_types"]),
        "schema_status": str(raw.get("schema_status") or "candidate"),
    }
