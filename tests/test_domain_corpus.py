# -*- coding: utf-8 -*-
"""领域语料驱动与语料硬门测试（HQC / 基于编码的密码学）。"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from research_agent import packs
from research_agent.db import connect, upsert_paper
from research_agent.ontology import store as ont
from tests._tmpdir import make_temp_dir

ROOT = Path(__file__).resolve().parents[1]


def _load_driver():
    """examples/ 不是包，用文件路径导入。"""
    path = ROOT / "examples" / "run_domain_corpus.py"
    spec = importlib.util.spec_from_file_location("run_domain_corpus", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_domain_corpus"] = module
    spec.loader.exec_module(module)
    return module


class CorpusFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = _load_driver()
        cls.groups = cls.driver.corpus_filter_terms("code_based_crypto")

    def test_pack_declares_two_required_groups(self):
        self.assertEqual(len(self.groups), 2)
        self.assertIn("hqc", self.groups[0])
        self.assertIn("kem", self.groups[1])

    def test_hqc_paper_is_in_scope(self):
        text = ("Exploiting Load/Store Leakage of Sparse Vectors for Key "
                "Recovery in HQC. We present a practical side-channel attack "
                "recovering the secret key of the HQC KEM.")
        self.assertTrue(self.driver.in_scope(text, self.groups))

    def test_title_only_hqc_without_crypto_word_is_out_of_scope(self):
        # 硬门按标题+摘要+正文整体判定；只有标题且既无 attack 也无 security
        # 等密码学词时不应放行（宁可事后人工补，也不放噪声进库）
        title = "Exploiting Load/Store Leakage of Sparse Vectors in HQC"
        self.assertFalse(self.driver.in_scope(title, self.groups))

    def test_code_based_paper_is_in_scope(self):
        title = "On the Independence Assumption in Quasi-Cyclic Code-Based Cryptography"
        self.assertTrue(self.driver.in_scope(title, self.groups))

    def test_mechanical_engineering_paper_is_out_of_scope(self):
        title = ("Synthesis of Mechanism for single- and hybrid-tasks using "
                 "Differential Evolution")
        self.assertFalse(self.driver.in_scope(title, self.groups))

    def test_lattice_only_kem_is_out_of_scope(self):
        title = "A Key Encapsulation Mechanism from Low Density Lattice Codes"
        self.assertFalse(self.driver.in_scope(title, self.groups))

    def test_empty_groups_accepts_everything(self):
        self.assertTrue(self.driver.in_scope("anything", []))

    def test_domain_without_filter_is_permissive(self):
        self.assertEqual(self.driver.corpus_filter_terms("chemistry"), [])

    def test_mark_out_of_scope_updates_status(self):
        tmp = make_temp_dir()
        try:
            db = Path(tmp.name) / "scope.db"
            conn = connect(db)
            ont.init_ontology(conn)
            upsert_paper(conn, {"paper_key": "good", "source": "arxiv",
                                "title": "HQC code-based KEM cryptanalysis",
                                "abstract": "syndrome decoding",
                                "clean_text": "text", "status": "ingested"})
            upsert_paper(conn, {"paper_key": "bad", "source": "arxiv",
                                "title": "Differential Evolution for Mechanism "
                                         "Synthesis",
                                "abstract": "optimization",
                                "clean_text": "text", "status": "ingested"})
            conn.commit()
            conn.close()
            result = self.driver.mark_out_of_scope(db, "code_based_crypto")
            self.assertEqual(result["dropped"], 1)
            conn = connect(db)
            try:
                rows = {r["paper_key"]: r["status"] for r in
                        conn.execute("SELECT paper_key, status FROM papers")}
                self.assertEqual(rows["bad"], "out_of_scope")
                self.assertEqual(rows["good"], "ingested")
            finally:
                conn.close()
        finally:
            tmp.cleanup()


class UnitSuffixNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_dir()
        self.db = Path(self.tmp.name) / "units.db"
        self.conn = connect(self.db)
        ont.init_ontology(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_unit_suffix_is_stripped_from_keys(self):
        hid, _ = ont.upsert_hyperedge(
            self.conn, hyperedge_type="failure_analysis", label="dfr study",
            members=[], conditions=[{"key": "n(bits)", "value": "17669"}],
            measurements=[{"metric": "dfr(log2)", "value": "-128", "unit": "log2"}],
            confidence=0.8, evidence_tier="primary", paper_key="p",
            provenance=[])
        self.conn.commit()
        cond = self.conn.execute(
            "SELECT condition_key, unit FROM ontology_hyperedge_conditions "
            "WHERE hyperedge_id=?", (hid,)).fetchone()
        meas = self.conn.execute(
            "SELECT metric, unit FROM ontology_hyperedge_measurements "
            "WHERE hyperedge_id=?", (hid,)).fetchone()
        self.assertEqual(meas["metric"], "dfr")
        self.assertEqual(meas["unit"], "log2")
        # 条件键 n(bits) 的 unit 未给出时保持原样（无法判定括号是否为单位）
        self.assertEqual(cond["condition_key"], "n(bits)")

    def test_plain_key_untouched(self):
        hid, _ = ont.upsert_hyperedge(
            self.conn, hyperedge_type="attack", label="isd",
            members=[], conditions=[], measurements=[
                {"metric": "work_factor", "value": "143", "unit": "log2"}],
            confidence=0.8, evidence_tier="primary", paper_key="p",
            provenance=[])
        self.conn.commit()
        meas = self.conn.execute(
            "SELECT metric FROM ontology_hyperedge_measurements "
            "WHERE hyperedge_id=?", (hid,)).fetchone()
        self.assertEqual(meas["metric"], "work_factor")


if __name__ == "__main__":
    unittest.main(verbosity=2)
