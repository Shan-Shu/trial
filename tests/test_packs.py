"""领域包/技能包加载层回归测试（v0.4.2）。

覆盖审计报告 `docs/HARDCODED_DOMAIN_CONTENT_AUDIT.md` 中"领域内容已外置"的验收：
- 领域判定、画像、种子类型、关系同义归一、机制词表、期刊分区全部来自 packs；
- 缺失包时**显式告警并返回空/缺省**，而不是回落到内联学科模板；
- 可通过 RA_PACKS_DIR / RA_JOURNAL_QUARTILES 覆盖。
"""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from research_agent import packs
from research_agent.domains import (
    EMPTY_PROFILE,
    infer_domain_kind,
    normalize_domain_profile,
)
from research_agent.ontology import store as ont
from research_agent.ontology.term_dictionaries import lookup_identity
from research_agent.quality.scoring import venue_factor
from research_agent.study.mechanism_lexicon import mechanism_lexicon
from research_agent.study.planner import infer_content_type, infer_task_kind
from tests._tmpdir import make_temp_dir


class PackLayerTest(unittest.TestCase):
    def test_builtin_packs_present(self):
        self.assertIn("chemistry", packs.available_domains())
        self.assertIn("general", packs.available_domains())
        for skill in ("journal-quartiles", "relation-lexicon",
                      "mechanism-keywords", "task-kind-hints"):
            self.assertIn(skill, packs.available_skills())
            self.assertTrue(packs.skill_data(skill), f"{skill} 数据为空")

    def test_domain_inference_from_packs(self):
        self.assertEqual(
            infer_domain_kind("", "提出一种炔酰胺合成多元氮杂环的新方法"),
            "chemistry")
        self.assertEqual(
            infer_domain_kind("", "战争胜利的伟力深藏在人民群众之中"),
            "humanities_social_science")
        # 未命中任何领域关键词时回落到声明 fallback 的领域
        self.assertEqual(infer_domain_kind("", "ZZZ 无关键词"), "general")

    def test_domain_profile_has_no_inline_template(self):
        profile = normalize_domain_profile(None, "", "炔酰胺环化")
        self.assertEqual(profile["domain_kind"], "chemistry")
        self.assertTrue(profile["dimensions"])
        self.assertIn("Reaction", profile["candidate_entity_types"])

    def test_seed_types_merge_core_and_domain(self):
        nodes = dict((k, v) for k, v in ont.seeded_node_types())
        relations = dict((k, v) for k, v in ont.seeded_relation_types())
        self.assertIn("Method", nodes)              # 核心种子
        self.assertIn("Catalyst", nodes)            # 化学领域包追加
        self.assertIn("uses", relations)            # 核心关系
        self.assertIn("affords", relations)         # 化学领域包追加

    def test_relation_synonyms_come_from_pack(self):
        self.assertEqual(ont.canonical_relation_type("utilizes"), "uses")
        self.assertEqual(ont.canonical_relation_type("novel_link"), "novel_link")

    def test_strong_relations_from_pack(self):
        strong = ont.strong_relation_set()
        self.assertIn("promotes", strong)
        self.assertNotIn("uses", strong)

    def test_mechanism_lexicon_merges_domain_vocab(self):
        chem = mechanism_lexicon("chemistry")
        hss = mechanism_lexicon("humanities_social_science")
        self.assertTrue(chem["mechanism_keywords"])
        self.assertTrue(chem["activation_rules"], "化学领域应有活化规则")
        # 人文学科没有化学机制增补：规则为空但不应崩溃，且已告警
        self.assertEqual(hss["activation_rules"], [])

    def test_journal_quartiles_and_scores(self):
        quartiles = packs.journal_quartiles()
        self.assertTrue(quartiles)
        scores = packs.quartile_scores()
        self.assertIn("Q1", scores)
        self.assertGreater(scores["Q1"], scores["Q2"])

    def test_unknown_journal_is_neutral_not_penalized(self):
        factor, quartile, _ = venue_factor(
            {"venue": "Some Unknown Journal", "source_type": "journal"})
        self.assertIsNone(quartile)
        self.assertGreaterEqual(factor, 0.5)

    def test_planner_hints_from_pack(self):
        self.assertEqual(infer_task_kind("写一份 RAG 综述"), "summary")
        self.assertEqual(infer_task_kind("提出一种新方法"), "generative")
        self.assertEqual(infer_content_type("写一份前沿综述"), "frontier_review")
        self.assertEqual(infer_content_type("设计一个实验方案"),
                         "experiment_protocol")

    def test_domain_dictionary_from_pack(self):
        hit = lookup_identity("H2O", node_type="Chemical")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["external_id"], "CHEBI:15377")
        self.assertIsNone(lookup_identity("完全不存在的术语XYZ"))


class PackOverrideTest(unittest.TestCase):
    """缺包 / 自定义包的行为：显式降级，不静默回落到代码内模板。"""

    def setUp(self):
        self.tmp = make_temp_dir()
        self._saved = {k: os.environ.get(k)
                       for k in ("RA_PACKS_DIR", "RA_JOURNAL_QUARTILES")}
        packs.reset_cache()

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        packs.reset_cache()
        self.tmp.cleanup()

    def _write_pack(self, root: Path, domain: str, payload: dict) -> None:
        target = root / "domains" / domain
        target.mkdir(parents=True, exist_ok=True)
        (target / "domain.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_custom_domain_pack_is_used(self):
        root = Path(self.tmp.name) / "packs"
        self._write_pack(root, "quantum", {
            "kind": "quantum",
            "keywords": ["qubit", "量子比特"],
            "profile": {"label": "量子信息", "dimensions": ["相干性"],
                        "candidate_entity_types": ["Qubit"],
                        "candidate_relation_types": ["couples_to"]},
        })
        os.environ["RA_PACKS_DIR"] = str(root)
        packs.reset_cache()
        self.assertIn("quantum", packs.available_domains())
        self.assertEqual(infer_domain_kind("", "研究 qubit 的相干时间"), "quantum")
        profile = normalize_domain_profile(None, "", "qubit 相干性")
        self.assertEqual(profile["label"], "量子信息")
        self.assertEqual(profile["candidate_relation_types"], ["couples_to"])

    def test_empty_pack_dir_degrades_explicitly(self):
        os.environ["RA_PACKS_DIR"] = str(Path(self.tmp.name) / "empty")
        os.environ["RA_PACKS_FALLBACK"] = ""
        packs.reset_cache()
        self.assertEqual(packs.available_domains(), [])
        self.assertIsNone(packs.domain_dir("chemistry"))
        profile = normalize_domain_profile(None, "", "任意请求")
        self.assertEqual(profile["domain_kind"], "general")
        # 空包下不应崩溃，且画像退化为空结构而不是某个学科模板
        self.assertIsInstance(profile["dimensions"], list)
        self.assertTrue(set(profile["candidate_entity_types"]) <=
                        set(EMPTY_PROFILE["candidate_entity_types"] or [])
                        or profile["candidate_entity_types"] == [])

    def test_journal_override_file(self):
        path = Path(self.tmp.name) / "jq.json"
        path.write_text(json.dumps({
            "journal of the american chemical society": "Q1"}), encoding="utf-8")
        os.environ["RA_JOURNAL_QUARTILES"] = str(path)
        packs.reset_cache()
        self.assertEqual(
            packs.journal_quartiles().get(
                "journal of the american chemical society"), "Q1")
        factor, quartile, _ = venue_factor({
            "venue": "Journal of the American Chemical Society",
            "source_type": "journal"})
        self.assertEqual(quartile, "Q1")
        self.assertAlmostEqual(factor, 0.95, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
