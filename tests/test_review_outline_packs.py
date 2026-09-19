# -*- coding: utf-8 -*-
"""综述写作结构（review-outline pack）与参考文献风格测试。"""
from __future__ import annotations

import unittest

from research_agent import packs
from research_agent.study.acs_format import (
    acs_reference_line,
    ieee_reference_line,
    reference_line,
    to_acs_document,
)
from research_agent.study.content import detect_writeup_language, render_review_block

CRYPTO = "code_based_crypto"

PAPER = {
    "paper_key": "arxiv:2401.00001",
    "title": "HQC: A Code-Based KEM",
    "venue": "IEEE Trans. Inf. Theory",
    "pub_year": 2024,
    "volume": "70",
    "issue": "3",
    "pages": "1234-1250",
    "doi": "https://doi.org/10.1109/TIT.2024.0001",
    "authors": [{"given": "Ada", "family": "Lovelace"},
                {"given": "Alan", "family": "Turing"}],
}


class ReviewOutlineTest(unittest.TestCase):
    def setUp(self):
        packs.reset_cache()

    def tearDown(self):
        packs.reset_cache()

    def test_base_outline_has_sections(self):
        outline = packs.review_outline("")
        self.assertIn("摘要", outline["sections"])
        self.assertIn("结论", outline["sections"])
        self.assertEqual(outline["language"], "zh")

    def test_crypto_outline_overrides_sections(self):
        outline = packs.review_outline(CRYPTO)
        joined = " ".join(outline["sections"])
        self.assertIn("HQC", joined)
        self.assertIn("ISD", joined)
        self.assertNotIn("成环与环加成策略", joined)

    def test_chemistry_outline_keeps_its_sections(self):
        outline = packs.review_outline("chemistry")
        self.assertIn("成环与环加成策略", outline["sections"])

    def test_english_language_switches_sections(self):
        outline = packs.review_outline(CRYPTO, "en")
        self.assertEqual(outline["language"], "en")
        self.assertIn("Abstract", outline["sections"])
        self.assertIn("Cryptanalysis: ISD-family and Decoding-Failure Attacks",
                      outline["sections"])

    def test_render_review_block_uses_domain_sections(self):
        plan = {"domain_profile": {"domain_kind": CRYPTO},
                "task_kind": "frontier_review"}
        block = render_review_block(plan, 8000, "写一份 HQC 综述")
        self.assertIn("## HQC：构造、编解码与参数集", block)
        self.assertIn("8000 个中文字符", block)
        self.assertNotIn("成环与环加成策略", block)
        self.assertNotIn("{", block.replace("{", "", 0))  # 占位符已全部替换
        self.assertNotIn("{sections}", block)

    def test_render_review_block_english(self):
        plan = {"domain_profile": {"domain_kind": CRYPTO},
                "task_kind": "frontier_review"}
        block = render_review_block(
            plan, 4000, "Write an IEEE style review in English")
        self.assertIn("## Abstract", block)
        self.assertIn("4000 words", block)
        self.assertIn("语言：English", block)

    def test_render_review_block_falls_back_without_domain(self):
        block = render_review_block({"task_kind": "summary"}, 3000, "")
        self.assertIn("## 摘要", block)
        self.assertIn("3000 个中文字符", block)

    def test_detect_writeup_language(self):
        self.assertEqual(detect_writeup_language("写一份中文综述"), "zh")
        self.assertEqual(detect_writeup_language("Write in English"), "en")
        self.assertEqual(detect_writeup_language("用英文撰写"), "en")
        self.assertEqual(
            detect_writeup_language("x", {"writeup_language": "en"}), "en")


class ReferenceStyleTest(unittest.TestCase):
    def test_acs_reference_line_default(self):
        line = acs_reference_line(PAPER, 3)
        self.assertTrue(line.startswith("(3) "))
        self.assertIn("HQC: A Code-Based KEM.", line)
        self.assertIn("*IEEE Trans. Inf. Theory*", line)
        self.assertIn("https://doi.org/10.1109/TIT.2024.0001", line)

    def test_ieee_reference_line(self):
        line = ieee_reference_line(PAPER, 3)
        self.assertTrue(line.startswith("[3] "))
        self.assertIn('"HQC: A Code-Based KEM,"', line)
        self.assertIn("vol. 70", line)
        self.assertIn("no. 3", line)
        self.assertIn("pp. 1234-1250", line)
        self.assertIn("doi: 10.1109/TIT.2024.0001", line)

    def test_reference_line_dispatch(self):
        self.assertEqual(reference_line(PAPER, 1),
                         acs_reference_line(PAPER, 1))
        self.assertEqual(reference_line(PAPER, 1, "ieee"),
                         ieee_reference_line(PAPER, 1))

    def test_to_document_honours_style_and_heading(self):
        draft = {
            "sections": [{
                "heading": "摘要",
                "items": [{"text": "HQC 是一种基于编码的 KEM。",
                           "pattern_ids": ["P-0001"], "evidence_ids": []}],
            }],
            "strategies": [],
        }
        knowledge = {
            "patterns": [{"pattern_id": "P-0001",
                          "paper_keys": [PAPER["paper_key"]],
                          "evidence_ids": ["E-0001-1"]}],
            "evidence": [{"evidence_id": "E-0001-1",
                          "paper_key": PAPER["paper_key"]}],
            "hyperedges": [],
        }
        papers = {PAPER["paper_key"]: PAPER}
        doc = to_acs_document(
            draft, knowledge, papers, title="T",
            keep_candidate_section=False,
            reference_style="ieee", references_heading="References")
        self.assertIn("## References", doc["markdown"])
        self.assertIn("[1] ", doc["markdown"])
        self.assertIn("<sup>1</sup>", doc["markdown"])
        self.assertEqual(doc["stats"]["references"], 1)

        doc_zh = to_acs_document(
            draft, knowledge, papers, title="T",
            keep_candidate_section=False,
            references_heading="参考文献")
        self.assertIn("## 参考文献", doc_zh["markdown"])
        self.assertTrue(doc_zh["references"][0].startswith("(1) "))


if __name__ == "__main__":
    unittest.main(verbosity=2)
