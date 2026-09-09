"""化学/术语领域词典的轻量本地层。

当前以少量稳定条目演示 IUPAC Gold Book 与 ChEBI 的归并方式；完整词典可扩展为
文件/API 加载。词典只负责给“Chemical/Method/Concept/Property”类节点提供外部身份，
不参与泛称合并，避免把不同配方/工艺错误并入。
"""
from __future__ import annotations

import re
from typing import Any


def _norm(value: str) -> str:
    value = re.sub(r"[\s_\-/\\.,;:'\"()\[\]{}]+", " ", str(value or "").strip())
    return re.sub(r"\s+", " ", value).strip().lower()


# key -> (chebi_id, canonical_name, aliases)
CHEBI_TERMS: dict[str, dict[str, Any]] = {
    "water": {
        "id": "CHEBI:15377",
        "canonical": "water",
        "aliases": ("H2O", "oxidane", "dihydrogen oxide"),
    },
    "hydrogen peroxide": {
        "id": "CHEBI:16240",
        "canonical": "hydrogen peroxide",
        "aliases": ("H2O2", "dioxidane"),
    },
    "dioxygen": {
        "id": "CHEBI:15379",
        "canonical": "dioxygen",
        "aliases": ("O2", "molecular oxygen"),
    },
    "carbon dioxide": {
        "id": "CHEBI:16526",
        "canonical": "carbon dioxide",
        "aliases": ("CO2",),
    },
    "methanol": {
        "id": "CHEBI:17790",
        "canonical": "methanol",
        "aliases": ("CH3OH", "methyl alcohol", "methyl hydroxide"),
    },
    "ethanol": {
        "id": "CHEBI:16236",
        "canonical": "ethanol",
        "aliases": ("CH3CH2OH", "ethyl alcohol"),
    },
    "ammonia": {
        "id": "CHEBI:16134",
        "canonical": "ammonia",
        "aliases": ("NH3",),
    },
    "sodium chloride": {
        "id": "CHEBI:26710",
        "canonical": "sodium chloride",
        "aliases": ("NaCl",),
    },
}

# IUPAC Gold Book 主要给 Method/Concept/Property 提供规范化术语拼写，无 ChEBI 式 ID。
GOLDBOOK_TERMS: dict[str, dict[str, Any]] = {
    "annulation": {
        "id": "annulation",
        "canonical": "annulation",
        "aliases": ("annulation reaction", "annulative"),
    },
    "catalysis": {
        "id": "catalysis",
        "canonical": "catalysis",
        "aliases": ("catalytic process", "catalytic reaction"),
    },
    "chemoselectivity": {
        "id": "chemoselectivity",
        "canonical": "chemoselectivity",
        "aliases": ("chemoselective",),
    },
    "regioselectivity": {
        "id": "regioselectivity",
        "canonical": "regioselectivity",
        "aliases": ("regioselective", "regiospecificity"),
    },
    "stereoselectivity": {
        "id": "stereoselectivity",
        "canonical": "stereoselectivity",
        "aliases": ("stereoselective", "stereospecificity"),
    },
    "electrophile": {
        "id": "electrophile",
        "canonical": "electrophile",
        "aliases": ("electrophilic reagent", "electrophilic species"),
    },
    "nucleophile": {
        "id": "nucleophile",
        "canonical": "nucleophile",
        "aliases": ("nucleophilic reagent", "nucleophilic species"),
    },
}

_CHEBI_LOOKUP: dict[str, str] = {}
_GOLD_LOOKUP: dict[str, str] = {}
for _canonical, _entry in CHEBI_TERMS.items():
    _CHEBI_LOOKUP[_norm(_canonical)] = _canonical
    for _alias in _entry["aliases"]:
        _CHEBI_LOOKUP[_norm(_alias)] = _canonical
for _canonical, _entry in GOLDBOOK_TERMS.items():
    _GOLD_LOOKUP[_norm(_canonical)] = _canonical
    for _alias in _entry["aliases"]:
        _GOLD_LOOKUP[_norm(_alias)] = _canonical


def lookup_identity(name: str,
                    *,
                    node_type: str = "",
                    aliases: list[str] | None = None,
                    source: str = "") -> dict[str, Any] | None:
    """返回领域词典身份；无匹配时返回 None，不进行猜测归并。"""
    ntype = str(node_type or "").strip().lower()
    source = str(source or "").lower()
    candidates = [name or ""] + [str(a) for a in aliases or []]
    if ntype == "chemical" or not source or source == "chebi":
        for text in candidates:
            canonical = _CHEBI_LOOKUP.get(_norm(text))
            if canonical:
                entry = CHEBI_TERMS[canonical]
                return {
                    "external_source": "chebi",
                    "external_id": entry["id"],
                    "canonical_name": entry["canonical"],
                }
    if ntype in ("method", "concept", "property", "chemical") \
            or not source or source == "iupac_gold_book":
        for text in candidates:
            canonical = _GOLD_LOOKUP.get(_norm(text))
            if canonical:
                entry = GOLDBOOK_TERMS[canonical]
                return {
                    "external_source": "iupac_gold_book",
                    "external_id": entry["id"],
                    "canonical_name": entry["canonical"],
                }
    return None


def iter_dictionary_terms():
    """供诊断/导出使用：返回(source, entry)。"""
    for canonical, entry in CHEBI_TERMS.items():
        yield "chebi", entry["id"], canonical, entry["aliases"]
    for canonical, entry in GOLDBOOK_TERMS.items():
        yield "iupac_gold_book", entry["id"], canonical, entry["aliases"]
