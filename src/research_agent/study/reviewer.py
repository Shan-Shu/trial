"""审核校对节点。

一级评审只有两个维度：
1. 用户指令符合度（80%）；
2. 证据与引用正确性（20%，带最低阈值）。

创新、归纳、总结、证明、格式等都属于用户指令符合度的具体检查项，不作为独立评分维度。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from langchain_core.messages import HumanMessage

from research_agent.config import Settings, settings as default_settings
from research_agent.study.content import render_draft
from research_agent.study.events import log_study_event
from research_agent.study.json_utils import clean_str, parse_json_object

logger = logging.getLogger(__name__)

REVIEW_PROMPT = """你是科研内容审核校对节点。你的职责不是补充内容，而是核查草稿是否完成用户指令，并核查证据和引用是否正确。

审核只有两个一级维度，禁止自行增加其他一级评分维度：
1. instruction_compliance：用户指令符合度，权重 80%。创新性、归纳、总结、证明、格式、数量等要求都放在这里逐项检查；只有用户明确提出创新性时才检查创新性。
2. evidence_correctness：证据与引用正确性，权重 20%。设置 correctness_threshold 最低阈值；低于阈值必须 revise 或 need_more_data。

给定材料：
1. user_request：用户原始指令；
2. instruction_contract：从指令中抽取的硬约束和软约束；
3. task_plan：规范化任务单；
4. knowledge：真实的 pattern/evidence/hyperedge 清单；
5. draft：待审核草稿。

硬性规则：
- supported 内容必须至少有一个真实 evidence_id 或 hyperedge_id；
- 不得引用不存在的 pattern_id / evidence_id / hyperedge_id；
- 不得新增 knowledge 之外的来源；
- 不得把 correlation 写成 causality；
- 不得把假设写成已证实事实；
- 缺少证据时给 need_more_data，格式或内容遗漏给 revise。

只输出 JSON 对象：
{
  "decision": "pass|revise|need_more_data|manual_review",
  "summary": "一句话结论",
  "instruction_compliance": {
    "score": 0.0,
    "requirements": [
      {
        "id": "R1",
        "category": "content|method|coverage|logic|format|safety",
        "type": "mandatory|optional",
        "requirement": "可判定的要求",
        "status": "met|partial|unmet|not_applicable",
        "location": "章节/位置",
        "reason": "判断理由",
        "revision_action": "需要修改时填写"
      }
    ],
    "critical_failures": []
  },
  "evidence_correctness": {
    "score": 0.0,
    "threshold": 0.85,
    "hard_fail": false,
    "checks": [
      {
        "type": "citation_exists|correct_attribution|no_fabrication|no_overclaim|no_unsupported_number|contradiction_preserved",
        "status": "pass|partial|fail",
        "location": "章节/位置",
        "reason": "问题说明"
      }
    ]
  },
  "revision_actions": []
}

user_request:
{request}

instruction_contract:
{contract}

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
    hyperedges = knowledge.get("hyperedges") or []
    pattern_ids = {str(p.get("pattern_id") or "") for p in patterns}
    evidence_ids = {str(e.get("evidence_id") or "") for e in evidence}
    evidence_ids |= {f"H-{int(h['hyperedge_id']):04d}"
                     for h in hyperedges if h.get("hyperedge_id") is not None}
    return pattern_ids, evidence_ids


def _normalize_contract(plan: dict[str, Any], request: str,
                        default_threshold: float = 0.85) -> dict[str, Any]:
    raw = plan.get("instruction_contract") or {}
    contract = plan.get("creative_contract") or {}
    deliverable = plan.get("deliverable") or {}
    required_count = raw.get("required_method_count")
    if required_count is None and plan.get("task_kind") == "generative":
        required_count = contract.get("min_candidates")
    threshold = raw.get("correctness_threshold", default_threshold)
    try:
        threshold = float(threshold)
    except (TypeError, ValueError):
        threshold = default_threshold
    return {
        "task_kind": clean_str(raw.get("task_kind"), plan.get("task_kind") or "research_report"),
        "deliverable_format": clean_str(raw.get("deliverable_format"), deliverable.get("format") or "markdown"),
        "language": clean_str(raw.get("language"), deliverable.get("language") or "zh"),
        "required_method_count": max(0, int(required_count or 0)),
        "required_sections": [str(x) for x in raw.get("required_sections") or [] if str(x).strip()],
        "must_include": [str(x) for x in raw.get("must_include") or [] if str(x).strip()],
        "must_exclude": [str(x) for x in raw.get("must_exclude") or [] if str(x).strip()],
        "reference_style": clean_str(raw.get("reference_style"), "ACS"),
        "evidence_policy": clean_str(raw.get("evidence_policy"), "每条实质断言可溯源"),
        "correctness_threshold": min(1.0, max(0.0, threshold)),
        "user_request": request,
    }


def _draft_markdown(draft: dict[str, Any]) -> str:
    return clean_str(draft.get("markdown")) or render_draft(draft)


def _requirement(rid: str, category: str, requirement: str, status: str,
                 location: str = "draft", reason: str = "",
                 severity: str = "major", mandatory: bool = True) -> dict[str, Any]:
    return {
        "id": rid,
        "category": category,
        "type": "mandatory" if mandatory else "optional",
        "requirement": requirement,
        "status": status,
        "location": location,
        "reason": reason,
        "severity": severity,
        "revision_action": "" if status == "met" else reason,
    }


def _instruction_review(plan: dict[str, Any], request: str,
                        draft: dict[str, Any]) -> dict[str, Any]:
    contract = _normalize_contract(plan, request)
    text = _draft_markdown(draft)
    lower = text.lower()
    requirements: list[dict[str, Any]] = []
    rid = 0

    req_count = int(contract["required_method_count"] or 0)
    if req_count:
        rid += 1
        actual = len(draft.get("strategies") or [])
        status = "met" if actual >= req_count else "unmet"
        requirements.append(_requirement(
            f"R{rid}", "method", f"至少提出 {req_count} 个候选方法", status,
            "strategies", f"实际 {actual} 个", "critical"))

    for section in contract["required_sections"]:
        rid += 1
        status = "met" if section.lower() in lower else "unmet"
        requirements.append(_requirement(
            f"R{rid}", "content", f"必须包含章节/内容：{section}", status,
            section, "未在草稿中识别到该内容", "major"))

    for term in contract["must_include"]:
        rid += 1
        status = "met" if term.lower() in lower else "unmet"
        requirements.append(_requirement(
            f"R{rid}", "content", f"必须包含：{term}", status,
            "draft", "草稿未包含指定对象或要求", "major"))

    for term in contract["must_exclude"]:
        rid += 1
        status = "unmet" if term.lower() in lower else "met"
        requirements.append(_requirement(
            f"R{rid}", "content", f"不得出现：{term}", status,
            "draft", "草稿出现禁止内容", "critical"))

    language = contract.get("language") or "zh"
    if language.startswith("zh"):
        rid += 1
        chinese = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        status = "met" if chinese >= max(20, len(text) * 0.1) else "partial"
        requirements.append(_requirement(
            f"R{rid}", "format", "使用中文输出", status,
            "draft", "中文字符比例偏低", "major"))

    rid += 1
    fmt = contract.get("deliverable_format") or "markdown"
    if fmt.lower() in ("markdown", "md"):
        status = "met" if text.strip() else "unmet"
        reason = "" if status == "met" else "草稿为空"
    else:
        status = "not_applicable"
        reason = "当前审核对象为结构化草稿，最终 DOCX 导出由交付环节检查"
    requirements.append(_requirement(
        f"R{rid}", "format", f"交付格式：{fmt}", status,
        "deliverable", reason, "major"))

    active = [r for r in requirements if r["status"] != "not_applicable"]
    score = sum(1.0 if r["status"] == "met" else 0.5 if r["status"] == "partial" else 0.0
                for r in active) / max(1, len(active))
    critical = [r for r in requirements if r["status"] != "met"
                and r.get("severity") == "critical"]
    return {
        "score": round(score, 3),
        "requirements": requirements,
        "critical_failures": critical,
        "contract": contract,
    }


def _evidence_review(plan: dict[str, Any], request: str,
                     draft: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    pattern_ids, evidence_ids = _reference_sets(knowledge)
    contract = _normalize_contract(plan, request)
    threshold = float(contract.get("correctness_threshold", 0.85))
    checks: list[dict[str, Any]] = []
    hard_fail = False

    def check(typ: str, status: str, location: str, reason: str) -> None:
        nonlocal hard_fail
        checks.append({"type": typ, "status": status,
                       "location": location, "reason": reason})
        if status == "fail" and typ in ("citation_exists", "correct_attribution",
                                         "no_fabrication"):
            hard_fail = True

    summary = draft.get("summary") or {}
    all_refs = set(str(x) for x in summary.get("pattern_ids") or [])
    all_refs |= set(str(x) for x in summary.get("evidence_ids") or [])
    for sec in draft.get("sections") or []:
        for item in sec.get("items") or []:
            all_refs |= {str(x) for x in item.get("pattern_ids") or []}
            all_refs |= {str(x) for x in item.get("evidence_ids") or []}
    invalid = sorted(x for x in all_refs if x and x not in pattern_ids | evidence_ids)
    check("citation_exists", "fail" if invalid else "pass", "draft",
          f"无效引用：{invalid}" if invalid else "")

    supported_missing = []
    for si, sec in enumerate(draft.get("sections") or []):
        for ii, item in enumerate(sec.get("items") or []):
            refs = list(item.get("pattern_ids") or []) + list(item.get("evidence_ids") or [])
            if item.get("status") == "supported" and not refs:
                supported_missing.append(f"section[{si}].items[{ii}]")
    check("correct_attribution", "fail" if supported_missing else "pass",
          "draft", f"supported 条目缺少引用：{supported_missing}" if supported_missing else "")

    failures = sum(1 for c in checks if c["status"] == "fail")
    partials = sum(1 for c in checks if c["status"] == "partial")
    score = max(0.0, 1.0 - 0.5 * failures - 0.1 * partials)
    if hard_fail:
        score = min(score, threshold - 0.01)
    return {
        "score": round(max(0.0, score), 3),
        "threshold": threshold,
        "hard_fail": hard_fail,
        "checks": checks,
    }


def deterministic_review(plan: dict[str, Any], draft: dict[str, Any],
                         knowledge: dict[str, Any],
                         request: str = "") -> dict[str, Any]:
    instruction = _instruction_review(plan, request, draft)
    evidence = _evidence_review(plan, request, draft, knowledge)
    overall = round(0.8 * instruction["score"] + 0.2 * evidence["score"], 3)
    issues = []
    for r in instruction["requirements"]:
        if r["status"] == "unmet":
            issues.append({"severity": r["severity"], "type": "instruction",
                           "location": r["location"], "problem": r["reason"]})
    for c in evidence["checks"]:
        if c["status"] == "fail":
            issues.append({"severity": "critical", "type": c["type"],
                           "location": c["location"], "problem": c["reason"]})
    revision_actions = [r["revision_action"] for r in instruction["requirements"]
                        if r["revision_action"]]
    if evidence["score"] < evidence["threshold"] or evidence["hard_fail"]:
        decision = "need_more_data" if evidence["hard_fail"] and not instruction["critical_failures"] else "revise"
    elif instruction["critical_failures"] or any(
            r["status"] == "unmet" for r in instruction["requirements"]):
        decision = "revise"
    else:
        decision = "pass"
    return {
        "decision": decision,
        "summary": f"指令符合度 {instruction['score']}；证据正确性 {evidence['score']}（阈值 {evidence['threshold']}）",
        "instruction_compliance": instruction,
        "evidence_correctness": evidence,
        "overall_score": overall,
        "issues": issues,
        "revision_actions": revision_actions,
    }


def normalize_review(data: dict[str, Any] | None,
                     fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    if not data:
        return fallback or {"decision": "revise", "summary": "无可解析审核结果",
                            "instruction_compliance": {"score": 0.0, "requirements": []},
                            "evidence_correctness": {"score": 0.0, "threshold": 0.85},
                            "overall_score": 0.0, "issues": [], "revision_actions": []}
    if "instruction_compliance" not in data or "evidence_correctness" not in data:
        return fallback or deterministic_review({}, {}, {})
    instruction = data.get("instruction_compliance") or {}
    evidence = data.get("evidence_correctness") or {}
    try:
        c_score = max(0.0, min(1.0, float(instruction.get("score") or 0.0)))
    except (TypeError, ValueError):
        c_score = 0.0
    try:
        e_score = max(0.0, min(1.0, float(evidence.get("score") or 0.0)))
    except (TypeError, ValueError):
        e_score = 0.0
    try:
        threshold = float(evidence.get("threshold") or 0.85)
    except (TypeError, ValueError):
        threshold = 0.85
    hard_fail = bool(evidence.get("hard_fail"))
    if not evidence.get("hard_fail"):
        hard_fail = any(str(c.get("type")) in ("citation_exists", "correct_attribution", "no_fabrication")
                        and str(c.get("status")) == "fail"
                        for c in evidence.get("checks") or [])
    instruction["score"] = round(c_score, 3)
    evidence["score"] = round(e_score, 3)
    evidence["threshold"] = threshold
    evidence["hard_fail"] = hard_fail
    overall = round(0.8 * c_score + 0.2 * e_score, 3)
    critical_failures = instruction.get("critical_failures") or []
    decision = clean_str(data.get("decision"), "revise")
    if decision not in ("pass", "revise", "need_more_data", "manual_review"):
        decision = "revise"
    if hard_fail or e_score < threshold:
        decision = "need_more_data" if hard_fail else "revise"
    elif critical_failures:
        decision = "revise"
    return {
        "decision": decision,
        "summary": clean_str(data.get("summary"), ""),
        "instruction_compliance": instruction,
        "evidence_correctness": evidence,
        "overall_score": overall,
        "issues": data.get("issues") or [],
        "revision_actions": data.get("revision_actions") or [],
    }


def _compact_knowledge(knowledge: dict[str, Any], draft: dict[str, Any]) -> dict[str, Any]:
    return {
        "patterns": (knowledge.get("patterns") or [])[:30],
        "evidence": (knowledge.get("evidence") or [])[:50],
        "hyperedges": (knowledge.get("hyperedges") or [])[:40],
    }


def make_review_node(model=None, max_rounds: int = 3,
                     conn: sqlite3.Connection | None = None,
                     settings: Settings | None = None):
    """构造 LangGraph 审核校对节点。model 为 None 时使用确定性审核。"""
    settings = settings or default_settings

    def review_node(state: dict) -> dict:
        plan = state.get("plan") or {}
        instruction_contract = dict(plan.get("instruction_contract") or {})
        instruction_contract.setdefault(
            "correctness_threshold",
            getattr(settings, "review_correctness_min", 0.85),
        )
        plan = {**plan, "instruction_contract": instruction_contract}
        draft = state.get("draft") or {}
        knowledge = state.get("knowledge") or {}
        request = state.get("request") or ""
        run_id = state.get("run_id")
        rounds = int(state.get("review_rounds") or 0)
        contract = _normalize_contract(
            plan, request, getattr(settings, "review_correctness_min", 0.85))
        log_study_event(conn, settings, "reviewer", run_id, "running",
                        {"round": rounds + 1})
        fallback = deterministic_review(plan, draft, knowledge, request)
        review = fallback
        if model is not None:
            prompt = REVIEW_PROMPT.replace(
                "{request}", json.dumps(request, ensure_ascii=False)
            ).replace(
                "{contract}", json.dumps(contract, ensure_ascii=False, indent=2)
            ).replace(
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
                    "strategies": draft.get("strategies") or [],
                    "markdown": render_draft(draft),
                }, ensure_ascii=False, indent=2),
            )
            try:
                msg = model.invoke([HumanMessage(content=prompt)])
                parsed = parse_json_object(getattr(msg, "content", str(msg)))
                review = normalize_review(parsed, fallback)
                # Deterministic hard constraints always override LLM scores.
                if fallback["instruction_compliance"].get("critical_failures"):
                    review["instruction_compliance"]["critical_failures"] = (
                        fallback["instruction_compliance"]["critical_failures"])
                    review["decision"] = "revise"
                if fallback["evidence_correctness"].get("hard_fail"):
                    review["evidence_correctness"] = fallback["evidence_correctness"]
                    review["decision"] = "need_more_data"
                    review["overall_score"] = round(
                        0.8 * review["instruction_compliance"]["score"]
                        + 0.2 * review["evidence_correctness"]["score"], 3)
            except Exception as exc:  # noqa: BLE001
                logger.warning("审核节点 LLM 调用失败，回退确定性审核: %s", exc)
        if review["decision"] != "pass" and rounds + 1 >= max(1, max_rounds):
            review = {**review, "decision": "manual_review",
                      "summary": f"{review.get('summary') or ''} | 已达最大审核轮数"}
        status = "manual_review" if review["decision"] == "manual_review" else "reviewed"
        log_study_event(conn, settings, "reviewer", run_id, status, {
            "decision": review["decision"],
            "issues": len(review.get("issues") or []),
            "instruction_score": review["instruction_compliance"].get("score"),
            "evidence_score": review["evidence_correctness"].get("score"),
            "correctness_threshold": review["evidence_correctness"].get("threshold"),
            "round": rounds + 1,
            "summary": review.get("summary"),
        })
        return {"review": review, "decision": review["decision"],
                "review_rounds": rounds + 1, "status": status}

    return review_node
