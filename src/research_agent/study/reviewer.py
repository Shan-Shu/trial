"""审核校对节点。

对内容形成节点生成的草稿做反向核查：每一条实质性观点必须引用知识消费节点
给出的真实 pattern_id / evidence_id；无法核查的内容不能以 supported 状态交付。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.messages import HumanMessage

from research_agent.study.content import render_draft
from research_agent.study.json_utils import clean_str, parse_json_object

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """你是科研内容审核校对节点。你的职责不是补充内容，而是核查内容形成节点
的草稿是否所有实质观点都可溯源。

给定材料：
1. task_plan：研究任务单；
2. draft：待审核草稿（含结构化 items 与 markdown）；
3. knowledge：知识消费节点返回的真实 pattern/evidence 清单。

核查规则：
1. item.status="supported" 时必须至少引用一个真实 evidence_id；
2. item 引用的 pattern_id / evidence_id 必须全部存在于 knowledge 中；
3. 原文是 correlation 时不得在草稿中写成 causality；
4. 草稿不得新增 knowledge 之外的引用或来源；
5. 若只是缺少证据，请指出 location，并让内容节点改为 revise 或标记 open_question；
6. 若任务需要的领域在当前语料中没有覆盖，返回 need_more_data 并说明缺口。

只输出 JSON 对象：
{{
  "decision": "pass|revise|need_more_data",
  "summary": "一句话审核结论",
  "issues": [
    {{
      "severity": "critical|minor",
      "type": "missing_evidence|bad_reference|overclaim|coverage_gap|other",
      "location": "标题/章节/item",
      "problem": "问题描述"
    }}
  ]
}}

task_plan:
{plan}

knowledge:
{knowledge}

draft:
{draft}

请直接输出 JSON："""


def _reference_sets(knowledge: dict[str, Any]) -> tuple[set[str], set[str]]:
    patterns = knowledge.get("patterns") or []
    evidence = knowledge.get("evidence") or []
    pattern_ids = {str(p.get("pattern_id") or "") for p in patterns}
    evidence_ids = {str(e.get("evidence_id") or "") for e in evidence}
    return pattern_ids, evidence_ids


def deterministic_review(plan: dict[str, Any],
                         draft: dict[str, Any],
                         knowledge: dict[str, Any]) -> dict[str, Any]:
    """无模型时的确定性审核：只做引用存在性和 supported 证据门控。"""
    pattern_ids, evidence_ids = _reference_sets(knowledge)
    issues: list[dict[str, Any]] = []
    sections = draft.get("sections") or []
    if not sections:
        issues.append({
            "severity": "critical",
            "type": "other",
            "location": "draft",
            "problem": "草稿没有任何 section",
        })
    summary = draft.get("summary") or {}
    summary_patterns = {str(x) for x in summary.get("pattern_ids") or []}
    summary_evidence = {str(x) for x in summary.get("evidence_ids") or []}
    if summary_patterns - pattern_ids:
        issues.append({
            "severity": "critical",
            "type": "bad_reference",
            "location": "summary",
            "problem": f"摘要引用不存在的 pattern_id: "
                       f"{sorted(summary_patterns - pattern_ids)}",
        })
    if summary_evidence - evidence_ids:
        issues.append({
            "severity": "critical",
            "type": "bad_reference",
            "location": "summary",
            "problem": f"摘要引用不存在的 evidence_id: "
                       f"{sorted(summary_evidence - evidence_ids)}",
        })
    for si, sec in enumerate(sections):
        for ii, item in enumerate(sec.get("items") or []):
            loc = f"section[{si}].items[{ii}]"
            refs = {str(x) for x in item.get("pattern_ids") or []}
            evs = {str(x) for x in item.get("evidence_ids") or []}
            status = clean_str(item.get("status"), "supported")
            missing_patterns = refs - pattern_ids
            missing_evidence = evs - evidence_ids
            if missing_patterns:
                issues.append({
                    "severity": "critical",
                    "type": "bad_reference",
                    "location": loc,
                    "problem": f"引用不存在的 pattern_id: {sorted(missing_patterns)}",
                })
            if missing_evidence:
                issues.append({
                    "severity": "critical",
                    "type": "bad_reference",
                    "location": loc,
                    "problem": f"引用不存在的 evidence_id: {sorted(missing_evidence)}",
                })
            if status in ("supported", "hypothesis") and not evs:
                issues.append({
                    "severity": "critical",
                    "type": "missing_evidence",
                    "location": loc,
                    "problem": f"{status} 状态但没有 evidence_id",
                })
    decision = "revise" if any(i["severity"] == "critical" for i in issues) else "pass"
    return {
        "decision": decision,
        "summary": (
            f"发现 {len(issues)} 个问题，其中 "
            f"{sum(1 for i in issues if i['severity'] == 'critical')} 个 critical"
            if issues else "草稿引用完整，可通过。"
        ),
        "issues": issues,
    }


def normalize_review(data: dict[str, Any] | None) -> dict[str, Any]:
    data = data or {}
    decision = clean_str(data.get("decision"), "revise")
    if decision not in ("pass", "revise", "need_more_data"):
        decision = "revise"
    return {
        "decision": decision,
        "summary": clean_str(data.get("summary"), ""),
        "issues": data.get("issues") or [],
    }


def _used_references(draft: dict[str, Any]) -> tuple[set[str], set[str]]:
    pattern_ids: set[str] = set()
    evidence_ids: set[str] = set()
    summary = draft.get("summary") or {}
    pattern_ids.update(str(x) for x in summary.get("pattern_ids") or [])
    evidence_ids.update(str(x) for x in summary.get("evidence_ids") or [])
    for sec in draft.get("sections") or []:
        for item in sec.get("items") or []:
            pattern_ids.update(str(x) for x in item.get("pattern_ids") or [])
            evidence_ids.update(str(x) for x in item.get("evidence_ids") or [])
    return pattern_ids, evidence_ids


def _compact_knowledge(knowledge: dict[str, Any],
                       draft: dict[str, Any]) -> dict[str, Any]:
    used_patterns, used_evidence = _used_references(draft)
    patterns = knowledge.get("patterns") or []
    evidence = knowledge.get("evidence") or []
    patterns_out = [
        p for p in patterns
        if str(p.get("pattern_id")) in used_patterns
    ]
    evidence_out = [
        e for e in evidence
        if str(e.get("evidence_id")) in used_evidence
    ]
    seen_p = {str(p.get("pattern_id")) for p in patterns_out}
    seen_e = {str(e.get("evidence_id")) for e in evidence_out}
    patterns_out += [p for p in patterns[:20] if str(p.get("pattern_id")) not in seen_p]
    evidence_out += [e for e in evidence[:30] if str(e.get("evidence_id")) not in seen_e]
    return {
        "patterns": patterns_out,
        "evidence": evidence_out,
    }


def make_review_node(model=None, max_rounds: int = 3):
    """构造 LangGraph 审核校对节点。model 为 None 时使用确定性审核。"""
    def review_node(state: dict) -> dict:
        plan = state.get("plan") or {}
        draft = state.get("draft") or {}
        knowledge = state.get("knowledge") or {}
        rounds = int(state.get("review_rounds") or 0)
        review = None
        if model is not None:
            prompt = REVIEW_PROMPT.replace(
                "{plan}", json.dumps(plan, ensure_ascii=False, indent=2)
            ).replace(
                "{knowledge}",
                json.dumps(_compact_knowledge(knowledge, draft),
                           ensure_ascii=False, indent=2),
            ).replace(
                "{draft}",
                json.dumps({
                    "title": draft.get("title"),
                    "sections": draft.get("sections"),
                    "markdown": render_draft(draft),
                }, ensure_ascii=False, indent=2),
            )
            try:
                msg = model.invoke([HumanMessage(content=prompt)])
                parsed = parse_json_object(getattr(msg, "content", str(msg)))
                review = normalize_review(parsed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("审核节点 LLM 调用失败，回退确定性审核: %s", exc)
        if review is None:
            review = deterministic_review(plan, draft, knowledge)
        if review["decision"] != "pass" and rounds + 1 >= max(1, max_rounds):
            review = {
                **review,
                "decision": "manual_review",
                "summary": (
                    f"{review.get('summary') or ''} | 已达最大审核轮数，转人工复核"
                ),
            }
        status = "manual_review" if review["decision"] == "manual_review" else "reviewed"
        return {
            "review": review,
            "decision": review["decision"],
            "review_rounds": rounds + 1,
            "status": status,
        }

    return review_node
