# -*- coding: utf-8 -*-
"""v0.0.6 知识提取二次精修（低置信/泛化关系）单元测试。"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage

from research_agent.config import Settings
from research_agent.knowledge.extractor import (
    KnowledgeExtractor,
    REFINE_PROMPT,
    build_prompt,
    flag_issues,
)


DIRTY = {
    "entities": [
        {"type": "Material", "name": "Calcium phosphate cement",
         "aliases": ["CPC"], "attributes": {},
         "confidence": 0.9, "evidence": "采用磷酸钙骨水泥(CPC)修复骨缺损。"},
        {"type": "Concept", "name": "These results suggest that CPC promotes osteogenesis",
         "aliases": [], "attributes": {},
         "confidence": 0.7, "evidence": "These results suggest that CPC promotes osteogenesis."},
    ],
    "relations": [
        {"type": "related_to", "subject": "Calcium phosphate cement",
         "predicate": "促进成骨", "object": "osteogenesis",
         "confidence": 0.6, "evidence": "These results suggest that CPC promotes osteogenesis."},
    ],
    "events": [],
}


CLEAN = {
    "entities": [
        {"type": "Material", "name": "Calcium phosphate cement",
         "aliases": ["CPC"], "attributes": {},
         "confidence": 0.9, "evidence": "采用磷酸钙骨水泥(CPC)修复骨缺损。"},
        {"type": "BiologicalProcess", "name": "osteogenesis",
         "aliases": [], "attributes": {},
         "confidence": 0.9, "evidence": "CPC promotes osteogenesis."},
    ],
    "relations": [
        {"type": "promotes", "subject": "Calcium phosphate cement",
         "predicate": "促进成骨", "object": "osteogenesis",
         "confidence": 0.9, "evidence": "CPC promotes osteogenesis."},
    ],
    "events": [],
}


class ScriptedModel:
    """按脚本依次返回内容的假模型。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[str] = []

    def invoke(self, messages, **kwargs) -> AIMessage:
        self.calls.append(messages[0].content if messages else "")
        if not self.responses:
            raise RuntimeError("没有更多脚本响应")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return AIMessage(content=nxt)


def _settings() -> Settings:
    s = Settings()
    s.knowledge_refine_enabled = True
    s.refine_min_conf = 0.75
    s.refine_max_items = 12
    s.refine_max_attempts = 2
    return s


def _extractor(model) -> KnowledgeExtractor:
    return KnowledgeExtractor(model, _settings())


def test_flag_issues_detects_reporting_entity_low_conf_and_generic():
    # min_conf=0.95：让 0.9 置信度的正常实体也被点名“低置信”，覆盖三类问题
    issues = flag_issues(DIRTY, min_conf=0.95, max_items=12)
    text = "\n".join(issues)
    assert any("These results suggest" in it for it in issues)      # 报告语实体
    assert any("置信度偏低" in it for it in issues)                  # 低置信
    assert any("related_to" in it and "偏泛化" in it for it in issues)


def test_extract_with_refine_improves_dirty_output():
    model = ScriptedModel([json.dumps(DIRTY, ensure_ascii=False),
                           json.dumps(CLEAN, ensure_ascii=False)])
    data, stats = _extractor(model).extract_with_refine(
        ["CPC promotes osteogenesis in bone defect repair."])
    assert stats["issues"] >= 2
    assert stats["attempts"] == 1
    assert stats["refined"] is True
    assert data["relations"][0]["type"] == "promotes"
    names = {e["name"] for e in data["entities"]}
    assert "These results suggest that CPC promotes osteogenesis" not in names
    # 首遍提示词包含 ERROR LIST，精修提示词包含“定向精修”
    assert "硬性禁区" in model.calls[0]
    assert "定向精修" in model.calls[1]


def test_extract_without_refine_when_clean():
    model = ScriptedModel([json.dumps(CLEAN, ensure_ascii=False)])
    data, stats = _extractor(model).extract_with_refine(
        ["CPC promotes osteogenesis in bone defect repair."])
    assert len(model.calls) == 1
    assert stats["issues"] == 0
    assert stats["refined"] is False
    assert data["relations"][0]["type"] == "promotes"


def test_refine_converges_when_unchanged():
    model = ScriptedModel([json.dumps(DIRTY, ensure_ascii=False),
                           json.dumps(DIRTY, ensure_ascii=False)])
    data, stats = _extractor(model).extract_with_refine(["some text"])
    assert stats["attempts"] == 1
    assert stats["converged"] is True
    assert stats["refined"] is False
    assert data == DIRTY


def test_refine_fallback_on_llm_error():
    model = ScriptedModel([json.dumps(DIRTY, ensure_ascii=False),
                           RuntimeError("refine call failed")])
    data, stats = _extractor(model).extract_with_refine(["some text"])
    assert stats["failed_attempts"] == 1
    assert stats["refined"] is False
    assert data == DIRTY


def test_build_prompt_includes_error_list_and_warning():
    p = build_prompt(["CPC promotes osteogenesis."],
                     {"title": "T", "venue": "V", "pub_year": 2025},
                     existing_entities=["Material: Calcium phosphate cement"],
                     recent_generic_warning="语料提醒：兜底关系已存在 20 条。")
    assert "硬性禁区" in p
    assert "语料提醒" in p
    assert "ERROR LIST #1" in p


def test_refine_prompt_has_required_markers():
    p = REFINE_PROMPT.format(
        header="论文: T",
        text="CPC promotes osteogenesis.",
        first_json=json.dumps(DIRTY, ensure_ascii=False),
        issues="- 问题示例",
    )
    assert "定向精修" in p
    assert "I am done" in p
    assert "首遍" in p
