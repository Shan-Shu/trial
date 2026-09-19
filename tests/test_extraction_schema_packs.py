# -*- coding: utf-8 -*-
"""抽取 schema 的 pack 化与 code_based_crypto 领域包测试。

覆盖 v0.4.5 新能力：领域包声明超边形式/条件键/度量指标/配额，代码只读取不内联；
以及 `code_based_crypto` 领域（HQC 等基于编码的密码学）的节点分布与超边设计。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from research_agent import packs
from research_agent.knowledge.extractor import build_prompt
from research_agent.study.mechanism_lexicon import mechanism_lexicon, reset_cache

ROOT = Path(__file__).resolve().parents[1]
CRYPTO = "code_based_crypto"


class ExtractionSchemaPackTest(unittest.TestCase):
    def setUp(self):
        packs.reset_cache()
        reset_cache()

    def tearDown(self):
        packs.reset_cache()
        reset_cache()

    def test_base_schema_loads(self):
        schema = packs.extraction_schema("")
        self.assertTrue(schema["entity_types"])
        self.assertTrue(schema["condition_keys"])
        self.assertTrue(schema["hyperedge_types"])
        self.assertEqual(schema["hyperedge_quota"]["event"], 120)

    def test_unknown_domain_falls_back_to_base_without_raising(self):
        schema = packs.extraction_schema("no_such_domain")
        self.assertTrue(schema["entity_types"])
        self.assertIn("temperature",
                      [c["key"] for c in schema["condition_keys"]])

    def test_crypto_types_are_additive(self):
        schema = packs.extraction_schema(CRYPTO)
        entities = schema["entity_types"]
        self.assertIn("Method", entities)          # 基础层仍在
        self.assertIn("Scheme", entities)          # 领域层追加
        self.assertIn("CodeFamily", entities)
        types = [h["type"] for h in schema["hyperedge_types"]]
        self.assertIn("claim", types)              # 基础超边类型
        self.assertIn("attack", types)             # 领域超边类型
        self.assertIn("failure_analysis", types)

    def test_crypto_quantities_override_base(self):
        schema = packs.extraction_schema(CRYPTO)
        cond = [c["key"] for c in schema["condition_keys"]]
        metrics = [m["metric"] for m in schema["measurement_metrics"]]
        # 覆盖语义：化学/生物的键不得出现在密码学提示词里
        self.assertNotIn("temperature", cond)
        self.assertNotIn("solvent", cond)
        self.assertNotIn("p_value", metrics)
        self.assertNotIn("yield", metrics)
        for key in ("security_level", "n", "w", "t", "attack_model"):
            self.assertIn(key, cond)
        for metric in ("work_factor", "dfr", "public_key_size", "decaps_cycles"):
            self.assertIn(metric, metrics)

    def test_quota_merges_base_and_domain(self):
        quota = packs.hyperedge_quota(CRYPTO)
        self.assertEqual(quota["event"], 60)       # 领域同名键覆盖基础层
        self.assertEqual(quota["relation"], 30)
        self.assertEqual(quota["attack"], 60)      # 领域新增类型
        self.assertEqual(quota["benchmark"], 30)
        base = packs.hyperedge_quota("general")
        self.assertEqual(base["event"], 120)       # 未被覆盖时用基础层
        self.assertNotIn("attack", base)

    def test_domain_inference_hits_hqc_text(self):
        text = ("HQC is a code-based key encapsulation mechanism whose security "
                "relies on the hardness of syndrome decoding.")
        self.assertEqual(packs.infer_domain_kind(text), CRYPTO)
        self.assertEqual(packs.infer_domain_kind("炔酰胺的环化反应与产率"), "chemistry")

    def test_prompt_renders_domain_hyperedge_forms(self):
        profile = {"domain_kind": CRYPTO, "label": "编码密码",
                   "schema_status": "frozen",
                   "candidate_entity_types": ["Scheme"],
                   "candidate_relation_types": ["based_on"]}
        prompt = build_prompt(["HQC text."], None, None, None, profile)
        self.assertIn("security_reduction", prompt)
        self.assertIn("work_factor", prompt)
        self.assertIn("decaps_cycles", prompt)
        self.assertIn("2^143", prompt)             # 领域量化示例
        self.assertNotIn("temperature(°C)", prompt)
        self.assertNotIn("mol%", prompt)
        self.assertNotIn("__", prompt, "占位符必须全部被替换")

    def test_prompt_without_domain_uses_base_only(self):
        profile = {"domain_kind": "general", "label": "通用",
                   "schema_status": "candidate",
                   "candidate_entity_types": [], "candidate_relation_types": []}
        prompt = build_prompt(["Some text."], None, None, None, profile)
        self.assertIn("temperature", prompt)
        self.assertNotIn("security_reduction", prompt)
        self.assertNotIn("__", prompt)

    def test_prompt_without_profile_still_renders(self):
        prompt = build_prompt(["Some text."])
        self.assertNotIn("__", prompt)
        self.assertIn("conditions", prompt)

    def test_mechanism_replace_mode_drops_other_discipline_terms(self):
        lex = mechanism_lexicon(CRYPTO)
        self.assertIn("information set decoding", lex["mechanism_keywords"])
        self.assertNotIn("umpolung", lex["mechanism_keywords"])
        self.assertIn("key_generation", lex["operator_keywords"])
        self.assertNotIn("polarity_reversal", lex["operator_keywords"])
        self.assertTrue(any("reaction attack" in k
                            for _, kw in lex["activation_rules"] for k in kw))

    def test_mechanism_append_mode_keeps_base_for_domains_without_replace(self):
        lex = mechanism_lexicon("chemistry")
        self.assertTrue(lex["mechanism_keywords"])

    def test_domain_pack_files_exist_and_are_readable(self):
        base = ROOT / "packs" / "domains" / CRYPTO
        self.assertTrue((base / "domain.json").is_file())
        self.assertTrue((base / "SKILL.md").is_file())
        self.assertTrue((base / "vocab" / "mechanism.jsonl").is_file())
        terms = packs.domain_terms(CRYPTO)
        self.assertGreaterEqual(len(terms), 30)
        for row in terms:
            self.assertTrue(str(row.get("term") or "").strip())
            self.assertTrue(str(row.get("id") or "").strip())
            self.assertTrue(str(row.get("source") or "").strip())

    def test_shared_extraction_schema_skill_is_documented(self):
        skill = ROOT / "packs" / "skills" / "extraction-schema"
        self.assertTrue((skill / "SKILL.md").is_file())
        data = json.loads((skill / "content" / "data.json").read_text("utf-8"))
        self.assertIn("entity_types", data)
        self.assertIn("hyperedge_quota", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
