# -*- coding: utf-8 -*-
"""用某个领域库生成综述（多版本），并把内部编号转成面向读者的引用。

三个版本（--variant all 时依次生成）：
  zh       中文 Markdown + 编号引用（ACS 顺序数字）
  zh_en    中文正文 + 英文摘要 + 编号引用
  en       英文正文 + IEEE 参考文献

用法::

    python examples/run_domain_review.py --db data/hqc_code_based_crypto.db \
        --domain code_based_crypto --out-dir output/hqc_review --variant all

说明：正文由研究链路（Planner → Consumer → Content → Reviewer → Fact-check）
生成，输入只有该库；本脚本负责版本化请求、编号转换与自检落盘。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_agent.config import Settings                     # noqa: E402
from research_agent.db import connect, get_paper               # noqa: E402
from research_agent.models import (                            # noqa: E402
    build_chat_model,
    build_role_model,
)
from research_agent.study.acs_format import (                  # noqa: E402
    _ANY_ID_RE,
    _PLACEHOLDER_TERMS,
    _SYSTEM_TERMS,
    polish_system_terms,
    to_acs_document,
)
from research_agent.study.graph import StudyServices, run_study  # noqa: E402

# 正文里偶发残留的裸库内编号（未被 [..] 包裹，替换器抓不到）
BARE_ID_RE = re.compile(r"(?<![A-Za-z0-9<])[PEH]-\d{1,6}(?:-\d{1,4})?(?![A-Za-z0-9>])")
POLISHED_ID_TEXT = "相关文献"


def polish_document(document: str) -> tuple[str, dict]:
    """收尾清理：裸库内编号 → “相关文献”，系统术语 → 面向读者表述。"""
    text = BARE_ID_RE.sub(POLISHED_ID_TEXT, document)
    text = polish_system_terms(text)
    checks = {
        "residual_internal_ids": sorted(set(_ANY_ID_RE.findall(text))),
        "system_terms": [t for t in _SYSTEM_TERMS if t in text],
        "placeholders": [t for t in _PLACEHOLDER_TERMS if t in text],
    }
    checks["clean"] = not (checks["residual_internal_ids"]
                           or checks["system_terms"] or checks["placeholders"])
    return text, checks


# 面向读者的综述里不该出现的"内部过程"小节（修订回应、候选池、自检等）
INTERNAL_SECTIONS = {
    "修订回应", "修订响应", "修订记录", "候选新方法", "候选方案", "自检",
    "内部说明", "事实核查", "fact-check", "revision responses",
    "candidate methods", "self-check",
}
# 摘要类小节：必须合成**一个**连贯段落，不能分条
ABSTRACT_SECTIONS = {"摘要", "abstract", "概要", "中文摘要", "英文摘要"}
_HEADING_RE = re.compile(r"^#{2,3}\s+(.*)$")
_ITEM_RE = re.compile(r"^[-*]\s+(.*)$")


def reformat_document(document: str) -> tuple[str, dict]:
    """把"要点罗列"的草稿排版改成成段论述。

    背景：内容节点输出的是 sections→items（每条是一个段落级文本），但此前的转换把
    每条 item 渲染成 markdown 项目符号（``- ...``），于是整篇综述看起来是"要点清单"
    而不是论文；摘要也成了分条。这里做纯文本级重排：

    1. 丢弃内部过程小节（修订回应/候选方案/自检等）；
    2. 每条 item 变成一个自然段（去掉项目符号）；
    3. 摘要类小节把各条合并为**一个**段落；
    4. 其余非 item 行（表格、引用行等）原样保留。
    """
    lines = document.splitlines()
    head: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            heading = m.group(1).strip()
            if heading.startswith("#"):
                heading = heading.lstrip("# ").strip()
            current = (heading, [])
            sections.append(current)
            continue
        if current is None:
            head.append(line)
        else:
            current[1].append(line)

    out = list(head)
    stats = {"internal_sections_dropped": 0, "paragraphs": 0,
             "bullets_removed": 0, "abstract_sections": 0}
    for heading, body in sections:
        low = heading.lower()
        if low in INTERNAL_SECTIONS or heading in INTERNAL_SECTIONS:
            stats["internal_sections_dropped"] += 1
            continue
        items: list[str] = []
        others: list[str] = []
        for line in body:
            mi = _ITEM_RE.match(line)
            if mi:
                items.append(mi.group(1).strip())
                stats["bullets_removed"] += 1
            else:
                others.append(line)
        out.append("")
        out.append(f"## {heading}")
        out.append("")
        if items and (low in ABSTRACT_SECTIONS or heading in ABSTRACT_SECTIONS):
            merged = " ".join(t for t in items if t)
            out.append(merged)
            out.append("")
            stats["paragraphs"] += 1
            stats["abstract_sections"] += 1
        else:
            for text in items:
                if text:
                    out.append(text)
                    out.append("")
                    stats["paragraphs"] += 1
        for line in others:
            if line.strip():
                out.append(line)
        # 去掉小节末尾多余空行
        while out and not out[-1].strip():
            out.pop()
    text = "\n".join(out).strip() + "\n"
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text, stats

TOPIC = "HQC 与基于编码的后量子密码（code-based cryptography）"

REQUESTS = {
    "zh": (
        "写一份题为《HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现》的中文综述论文。"
        "要求：只以给定文献库为事实来源；覆盖基于编码密码学的整体脉络与 HQC 的具体构造、"
        "码族与参数集、安全归约与困难性假设、ISD 族攻击与解码失败攻击、实现与侧信道、"
        "标准化与迁移、性能代价对比、挑战与开放问题；每条实质结论都要带库内编号引用，"
        "不得编造文献或数据，缺证据处明确写成开放问题。"
    ),
    "zh_en": (
        "写一份题为《HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现》的综述论文，"
        "正文用中文，并在摘要之后附一段 200 词左右的英文摘要（Abstract，另起小节）。"
        "要求：只以给定文献库为事实来源；覆盖基于编码密码学整体脉络与 HQC 的构造、参数集、"
        "安全归约、ISD 族攻击与解码失败攻击、实现与侧信道、标准化、性能对比与开放问题；"
        "每条实质结论都要带库内编号引用，不得编造文献或数据。"
    ),
    "en": (
        "Write a review paper in English, IEEE/ACM style, titled "
        "\"HQC and Code-Based Post-Quantum Cryptography: Constructions, Security, "
        "Cryptanalysis and Implementations\". "
        "Use ONLY the provided literature database as the source of facts. Cover the "
        "code-based cryptography landscape, HQC's construction, code families and "
        "parameter sets, security reductions and hardness assumptions, ISD-family and "
        "decoding-failure cryptanalysis, implementations and side channels, "
        "standardization and migration, performance/cost comparison, and open problems. "
        "Every substantive claim must carry an internal citation id; never invent "
        "references or numbers."
    ),
}

TITLES = {
    "zh": "HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现",
    "zh_en": "HQC 与基于编码的后量子密码：构造、安全性、密码分析与实现",
    "en": ("HQC and Code-Based Post-Quantum Cryptography: Constructions, Security, "
           "Cryptanalysis and Implementations"),
}

# 目标篇幅（中文按字符，英文按词）
TARGET_CHARS = {"zh": 9000, "zh_en": 9000, "en": 4000}
AUTHOR_BLOCK = {
    "zh": "本文由 research-agent 研究链路基于现有文献自动生成；引用编号对应文末参考文献。",
    "zh_en": ("本文由 research-agent 研究链路基于现有文献自动生成；正文中文，摘要英文；"
              "引用编号对应文末参考文献。"),
    "en": ("Generated by the research-agent pipeline from the local literature "
           "database. Numeric citations refer to the reference list."),
}


def load_papers(db: Path, keys: list[str]) -> dict[str, dict]:
    conn = connect(db)
    try:
        out = {}
        for key in keys:
            rec = get_paper(conn, key)
            if rec:
                out[key] = rec
        return out
    finally:
        conn.close()


def collect_referenced_keys(knowledge: dict, draft: dict) -> list[str]:
    """从知识包 + 草稿里收集所有可能被引用的论文 key。"""
    keys: list[str] = []
    for pattern in knowledge.get("patterns") or []:
        for pid in pattern.get("evidence_ids") or []:
            pass
    for item in knowledge.get("evidence") or []:
        key = str(item.get("paper_key") or "")
        if key and key != "unknown" and key not in keys:
            keys.append(key)
    for h in knowledge.get("hyperedges") or []:
        for ev in h.get("evidence") or []:
            key = str(ev.get("paper_key") or "")
            if key and key not in keys:
                keys.append(key)
    for key in knowledge.get("paper_keys") or []:
        if key not in keys:
            keys.append(key)
    return keys


def run_variant(db: Path, domain: str, variant: str, out_dir: Path,
                settings: Settings, persist: bool,
                fast_roles: bool = False) -> dict:
    request = REQUESTS[variant]
    print(f"\n===== 生成 {variant} 版综述 =====", flush=True)
    services = StudyServices(settings=settings)
    for role in ("planner", "consumer", "content", "review", "fact_check"):
        try:
            # 正文写作（content）用 .env 绑定的强模型；其余角色可选 flash 提速
            if fast_roles and role != "content":
                model = build_chat_model(provider="deepseek",
                                         model_name="deepseek-v4-flash",
                                         temperature=0.2)
            else:
                model = build_role_model(role)
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] {role} 模型绑定失败：{exc}", flush=True)
            model = None
        setattr(services, f"{role}_model", model)
    services.collector = None  # 只用本地库
    out = run_study(request, services=services, conn=None,
                    force_collect=False,
                    budget_override={"review_target_chars":
                                     TARGET_CHARS.get(variant, 9000)},
                    persist=persist)
    draft = out.get("draft") or {}
    knowledge = out.get("knowledge") or {}
    keys = collect_referenced_keys(knowledge, draft)
    papers = load_papers(db, keys)
    style = "ieee" if variant == "en" else "acs"
    heading = "References" if variant == "en" else "参考文献"
    doc = to_acs_document(
        draft, knowledge, papers,
        title=TITLES[variant],
        subtitle=TOPIC,
        header_notes=[AUTHOR_BLOCK[variant]],
        keep_candidate_section=False,
        reference_style=style,
        references_heading=heading,
    )
    payload = {
        "variant": variant,
        "request": request,
        "run_id": out.get("run_id"),
        "status": out.get("status"),
        "decision": out.get("decision"),
        "review": out.get("review"),
        "fact_check": out.get("fact_check"),
        "plan": out.get("plan"),
        "document": doc["markdown"],
        "references": doc["references"],
        "citation_map": doc["citation_map"],
        "checks": doc["checks"],
        "stats": doc["stats"],
        "draft_json": draft,
    }
    with_abstract, abstract_inserted = ensure_abstract(doc["markdown"], draft,
                                                       variant)
    reformatted, fmt_stats = reformat_document(with_abstract)
    polished, polish_checks = polish_document(reformatted)
    polish_checks["bullet_lines"] = len(
        [ln for ln in polished.splitlines() if _ITEM_RE.match(ln)])
    polish_checks["paragraph_style"] = polish_checks["bullet_lines"] == 0
    polish_checks["abstract_present"] = any(h in polished
                                            for h in ABSTRACT_HEADINGS)
    polish_checks["abstract_inserted"] = abstract_inserted
    payload["document"] = polished
    payload["format_stats"] = fmt_stats
    payload["checks"].update(polish_checks)
    doc["markdown"] = polished
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"review_{variant}.md").write_text(doc["markdown"],
                                                  encoding="utf-8")
    (out_dir / f"review_{variant}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{variant}] 正文 {doc['stats']['body_chars']} 字符 | 中文字符 "
          f"{doc['stats']['cjk_chars']} | 参考文献 {doc['stats']['references']} 条 | "
          f"自检 clean={doc['checks']['clean']}", flush=True)
    if not doc["checks"]["clean"]:
        print(f"[{variant}] 自检问题: {json.dumps(doc['checks'], ensure_ascii=False)}",
              flush=True)
    return payload


def ensure_abstract(document: str, draft: dict, variant: str) -> tuple[str, bool]:
    """综述必须有摘要：草稿的 ``summary`` 此前没有被渲染进正文，这里补上。

    已有 ``## 摘要`` / ``## Abstract`` 小节时不重复插入。
    """
    summary = (draft or {}).get("summary") or {}
    text = " ".join(str(x) for x in
                    ([summary.get("text")] + list(summary.get("items") or []))
                    if x)
    if not text.strip() or any(h in document for h in ABSTRACT_HEADINGS):
        return document, False
    heading = "## 摘要" if variant == "zh" else "## Abstract"
    marker = document.find("\n## ")
    block = f"{heading}\n\n{text.strip()}\n"
    if marker == -1:
        return document.rstrip() + "\n\n" + block, True
    return document[:marker].rstrip() + "\n\n" + block + document[marker:], True


ABSTRACT_HEADINGS = ("## 摘要", "## Abstract", "## 概要")


def polish_only(out_dir: Path) -> int:
    """只对已生成的综述文件做收尾：补摘要 + 段落化重排 + 裸编号/系统术语清理（不重跑 LLM）。"""
    fixed = 0
    for json_path in sorted(out_dir.glob("review_*.json")):
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        text, inserted = ensure_abstract(payload.get("document") or "",
                                         payload.get("draft_json") or {},
                                         str(payload.get("variant") or ""))
        text, fmt = reformat_document(text)
        text, checks = polish_document(text)
        bullet_lines = len([ln for ln in text.splitlines()
                            if _ITEM_RE.match(ln)])
        checks["bullet_lines"] = bullet_lines
        checks["paragraph_style"] = bullet_lines == 0
        checks["abstract_present"] = any(h in text for h in ABSTRACT_HEADINGS)
        checks["abstract_inserted"] = inserted
        payload["document"] = text
        payload.setdefault("checks", {}).update(checks)
        payload["format_stats"] = fmt
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        json_path.with_suffix(".md").write_text(text, encoding="utf-8")
        print(f"[polish] {json_path.name}: paragraphs={fmt['paragraphs']} "
              f"bullets_removed={fmt['bullets_removed']} "
              f"internal_dropped={fmt['internal_sections_dropped']} "
              f"abstract={checks['abstract_present']} "
              f"clean={checks['clean']} bullet_lines={bullet_lines}", flush=True)
        fixed += 1
    return fixed


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="领域库 → 多版本综述")
    ap.add_argument("--db", required=True)
    ap.add_argument("--domain", default="code_based_crypto")
    ap.add_argument("--out-dir", default="output/domain_review")
    ap.add_argument("--variant", default="all",
                    choices=["all", "zh", "zh_en", "en"])
    ap.add_argument("--no-persist", action="store_true",
                    help="不写 study_runs（默认写，便于审计）")
    ap.add_argument("--fast-roles", action="store_true",
                    help="Planner/Consumer/Reviewer/Fact-check 用 flash（正文仍用 .env 模型）")
    ap.add_argument("--polish-only", action="store_true",
                    help="只清理已生成的综述文件（不调用 LLM）")
    args = ap.parse_args(argv)

    if args.polish_only:
        return 0 if polish_only(Path(args.out_dir)) >= 0 else 1

    db = Path(args.db)
    out_dir = Path(args.out_dir)
    settings = Settings(db_path=db)
    settings.study_max_review_rounds = 2
    variants = (["zh", "zh_en", "en"] if args.variant == "all"
                else [args.variant])
    results = []
    for variant in variants:
        try:
            results.append(run_variant(db, args.domain, variant, out_dir,
                                       settings, not args.no_persist,
                                       fast_roles=args.fast_roles))
        except Exception as exc:  # noqa: BLE001
            logging.exception("生成 %s 版失败", variant)
            print(f"[error] {variant}: {exc}", flush=True)
    summary = [{"variant": r["variant"], "status": r["status"],
                "decision": r["decision"], "stats": r["stats"],
                "checks": r["checks"]} for r in results]
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===", flush=True)
    for row in summary:
        print(f"{row['variant']:6s} status={row['status']} "
              f"decision={row['decision']} refs={row['stats']['references']} "
              f"clean={row['checks']['clean']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
