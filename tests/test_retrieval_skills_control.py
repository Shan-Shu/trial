# -*- coding: utf-8 -*-
"""检索专项 skill 与质量控制全局归并测试。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from research_agent.config import Settings
from research_agent.db import connect
from research_agent.ontology import store as ont
from research_agent.quality.control import (
    maybe_global_merge,
    run_dictionary_merge,
)
from research_agent.retrieval.skills import (
    infer_retrieval_strategy,
    normalize_edge_gaps,
    plan_evidence_gap_queries,
    select_low_support_gaps,
)
from research_agent.study.content import make_content_node
from research_agent.study.planner import deterministic_plan


class RetrievalSkillsControlTest(unittest.TestCase):
    def test_planner_evidence_gap_inference(self):
        plan = deterministic_plan(
            "请对骨修复材料促进成骨的证据做多源补强和共识验证")
        self.assertEqual(plan["retrieval"]["strategy"], "evidence_gap")
        self.assertTrue(plan["retrieval"]["evidence_gap_enabled"])
        self.assertFalse(plan["retrieval"]["deep_single_domain_enabled"])

    def test_planner_deep_single_domain_inference(self):
        plan = deterministic_plan(
            "对炔酰胺催化环化这一单领域做精深挖掘并追溯参考文献")
        self.assertEqual(plan["retrieval"]["strategy"], "deep_single_domain")
        self.assertTrue(plan["retrieval"]["deep_single_domain_enabled"])

    def test_content_returns_low_support_edge_gaps(self):
        knowledge = {
            "patterns": [
                {
                    "pattern_id": "P-0001",
                    "source_type": "Material",
                    "source_name": "CPC",
                    "relation_type": "promotes",
                    "target_type": "BiologicalProcess",
                    "target_name": "osteogenesis",
                    "support_count": 1,
                },
                {
                    "pattern_id": "P-0002",
                    "source_type": "Material",
                    "source_name": "Hydrogel",
                    "relation_type": "is_a",
                    "target_type": "Material",
                    "target_name": "Biomaterial",
                    "support_count": 1,
                },
            ],
            "evidence": [],
        }
        node = make_content_node(None)
        out = node({
            "plan": {
                "goal": "test",
                "mission": {"seed_terms": ["CPC"]},
                "retrieval": {"min_support_target": 2},
            },
            "knowledge": knowledge,
        })
        ids = [g["pattern_id"] for g in out["edge_gaps"]]
        self.assertEqual(ids, ["P-0001"])

    def test_gap_query_plan_keeps_entity_constraint(self):
        gaps = normalize_edge_gaps([{
            "pattern_id": "P-9",
            "source_type": "Material",
            "source_name": "Calcium phosphate cement",
            "relation_type": "promotes",
            "target_type": "BiologicalProcess",
            "target_name": "osteogenesis",
            "support_count": 1,
        }])
        plans = plan_evidence_gap_queries(gaps)
        self.assertTrue(plans)
        self.assertIn("Calcium phosphate cement", plans[0]["query"])
        self.assertIn("osteogenesis", plans[0]["query"])
        self.assertEqual(plans[0]["pattern_id"], "P-9")

    def test_select_low_support_skips_taxonomy_edge(self):
        gaps = select_low_support_gaps([
            {
                "pattern_id": "P-1",
                "source_type": "Method",
                "source_name": "A",
                "relation_type": "uses",
                "target_type": "Task",
                "target_name": "B",
                "support_count": 1,
            },
            {
                "pattern_id": "P-2",
                "source_type": "Material",
                "source_name": "A",
                "relation_type": "is_a",
                "target_type": "Material",
                "target_name": "B",
                "support_count": 1,
            },
        ])
        self.assertEqual([g["pattern_id"] for g in gaps], ["P-1"])

    def test_dictionary_merge_repoints_edges(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "merge.db"
        conn = connect(path)
        ont.init_ontology(conn)
        try:
            tgt, _ = ont.upsert_node(
                conn, node_type="BiologicalProcess", name="osteogenesis",
                confidence=0.9)
            src1, _ = ont.upsert_node(
                conn, node_type="Chemical", name="water",
                confidence=0.9,
                provenance=[{"paper": "p1", "evidence": "water"}])
            src2, _ = ont.upsert_node(
                conn, node_type="Chemical", name="H2O",
                confidence=0.8,
                provenance=[{"paper": "p2", "evidence": "H2O"}])
            self.assertNotEqual(src1, src2)
            ont.upsert_edge(
                conn, relation_type="promotes", src_id=src1, tgt_id=tgt,
                confidence=0.9,
                provenance=[{"paper": "p1", "evidence": "water promotes"}])
            ont.upsert_edge(
                conn, relation_type="promotes", src_id=src2, tgt_id=tgt,
                confidence=0.8,
                provenance=[{"paper": "p2", "evidence": "H2O promotes"}])
            conn.commit()
            stats = run_dictionary_merge(conn)
            self.assertGreaterEqual(stats["merged_nodes"], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ontology_nodes").fetchone()[0],
                2,
            )
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ontology_edges").fetchone()[0],
                1,
            )
            edge = conn.execute(
                "SELECT provenance FROM ontology_edges"
            ).fetchone()
            self.assertEqual(len(json.loads(edge["provenance"])), 2)
        finally:
            conn.close()
            tmp.cleanup()

    def test_maybe_global_merge_threshold(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "threshold.db"
        conn = connect(path)
        ont.init_ontology(conn)
        settings = Settings(db_path=path)
        settings.global_merge_interval_nodes = 2
        try:
            ont.upsert_node(conn, node_type="Chemical", name="water",
                            confidence=0.9)
            conn.commit()
            first = maybe_global_merge(conn, settings)
            self.assertFalse(first["triggered"])
            ont.upsert_node(conn, node_type="Chemical", name="H2O",
                            confidence=0.8)
            conn.commit()
            second = maybe_global_merge(conn, settings)
            self.assertTrue(second["triggered"])
        finally:
            conn.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main(verbosity=2)
