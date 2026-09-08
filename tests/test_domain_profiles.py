"""领域画像优化测试：规划节点生成 profile，检索/抽取使用 profile。"""
from __future__ import annotations

import json
import unittest

from langchain_core.messages import AIMessage

from research_agent.domains import normalize_domain_profile
from research_agent.knowledge.extractor import build_prompt
from research_agent.quality.llm import QUALITY_PROMPT_TEMPLATE
from research_agent.retrieval.llm import RetrievalLLM, PLAN_PROMPT_TEMPLATE
from research_agent.study.planner import deterministic_plan


class DomainProfileTest(unittest.TestCase):
    def test_chemistry_profile_in_plan(self):
        plan = deterministic_plan(
            "尝试提出一种炔酰胺合成多元氮杂化合物的新方法")
        profile = plan["domain_profile"]
        self.assertEqual(profile["domain_kind"], "chemistry")
        self.assertIn("催化剂与试剂", profile["dimensions"])
        self.assertIn("Reaction", profile["candidate_entity_types"])
        self.assertIn("catalyzed_by", profile["candidate_relation_types"])

    def test_normalize_profile_falls_back(self):
        p = normalize_domain_profile(
            {"domain_kind": "biomedicine", "dimensions": ["适应症", "机制"]},
            "biomedical", "")
        self.assertEqual(p["domain_kind"], "biomedicine")
        self.assertEqual(p["dimensions"], ["适应症", "机制"])

    def test_retrieval_prompt_no_biomedical_hardcode(self):
        prompt = PLAN_PROMPT_TEMPLATE
        self.assertNotIn("适应症", prompt)
        self.assertNotIn("生物相容性", prompt)
        self.assertNotIn("临床转化", prompt)

    def test_extract_prompt_injects_domain_schema(self):
        profile = {
            "domain_kind": "chemistry",
            "label": "化学合成",
            "schema_status": "frozen",
            "candidate_entity_types": ["Reaction", "Substrate", "Catalyst"],
            "candidate_relation_types": ["catalyzed_by", "affords"],
        }
        prompt = build_prompt(["Reaction text."], None, None, None, profile)
        self.assertIn("Reaction", prompt)
        self.assertIn("catalyzed_by", prompt)
        self.assertIn("已冻结 schema", prompt)

    def test_quality_prompt_no_estimated_missing_data(self):
        self.assertNotIn("按期刊水平估计", QUALITY_PROMPT_TEMPLATE)
        self.assertIn("不得估计真实被引次数", QUALITY_PROMPT_TEMPLATE)


class _QueryModel:
    def __init__(self):
        self.prompt = ""

    def invoke(self, messages, **kwargs):
        self.prompt = messages[0].content
        return AIMessage(content='["ynamide annulation"]')


class RetrievalDimensionTest(unittest.TestCase):
    def test_plan_queries_receives_dimensions(self):
        model = _QueryModel()
        qs = RetrievalLLM(model).plan_queries(
            "ynamide", ["催化", "区域选择性", "反应机理"])
        self.assertEqual(qs, ["ynamide annulation"])
        self.assertIn("- 催化", model.prompt)
        self.assertIn("- 区域选择性", model.prompt)
        self.assertNotIn("适应症", model.prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
