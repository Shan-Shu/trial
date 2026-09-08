"""内容形成节点。

从知识消费节点返回的模式卡/证据卡中归纳论点并成稿。节点不查询数据库，
不新增证据 ID；每个实质性观点必须引用给定的 evidence_id。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from langchain_core.messages import HumanMessage

from research_agent.config import Settings, settings as default_settings
from research_agent.study.events import log_study_event
from research_agent.study.json_utils import clean_str, parse_json_object

logger = logging.getLogger(__name__)

CONTENT_PROMPT = """你是科研内容形成节点。工作方式是“先看证据，再形成结构”，
不允许先假设章节再找论据。

输入材料：
1. 研究任务单 task_plan；
2. 知识消费节点返回的模式卡 patterns 与证据卡 evidence。

要求：
1. 从 patterns 中观察高支持度、高置信度、同关系聚合的模式，再归纳章节和论点；
2. 每条实质性论点必须引用真实存在的 pattern_id / evidence_id；
3. 无法被证据支撑但值得提出的内容标为 status="open_question"，不要伪造证据；
4. 不要把 correlation 写成 causality；
5. 只输出 JSON 对象，不要代码块、不要解释。

task_plan:
{plan}

knowledge:
{knowledge}

输出 JSON 结构：
{{
  "title": "内容标题",
  "summary": {{
    "text": "一段摘要",
    "pattern_ids": [],
    "evidence_ids": []
  }},
  "sections": [
    {{
      "heading": "由证据归纳出的章节名",
      "items": [
        {{
          "text": "具体观点",
          "pattern_ids": ["P-xxxx"],
          "evidence_ids": ["E-xxxx"],
          "status": "supported|hypothesis|open_question"
        }}
      ]
    }}
  ]
}}

请直接输出 JSON："""

GENERATIVE_BLOCK = """
附加生成要求：
当前任务为 generative，不能只做“文献归纳”。你必须在 sections 之外额外生成
strategies 数组，至少 {min_candidates} 个差异化的候选方案。

每个候选方案应说明：
1. 目标：希望得到的新对象/新方法/新框架；
2. 使用的已有组件或方法；
3. 采用的创造操作（组合、迁移、替换、扩展、设计流水线等）；
4. 创新来源：为什么不是已有方案的同义改写；
5. 依据：哪些 pattern/evidence 支持其组成部件；
6. status 必须为 hypothesis。

strategies JSON 结构：
{{
  "strategies": [
    {{
      "id": "S-01",
      "title": "候选方案名称",
      "target": "目标对象/方案",
      "components": ["已有组件A", "已有组件B"],
      "creative_operation": "组合/迁移/替换/扩展/新流水线",
      "novelty_source": "为什么新",
      "rationale": "为什么可能可行",
      "pattern_ids": [],
      "evidence_ids": [],
      "status": "hypothesis",
      "risks": [],
      "validation_plan": "如何验证"
    }}
  ]
}}
"""


def render_draft(draft: dict[str, Any]) -> str:
    """把结构化草稿渲染为最终 markdown，保证文本和证据一致。"""
    lines = [f"# {clean_str(draft.get('title'), '未命名研究内容')}"]
    summary = draft.get("summary") or {}
    if clean_str(summary.get("text")):
        lines.append("")
        lines.append(clean_str(summary["text"]))
    for section in draft.get("sections") or []:
        heading = clean_str(section.get("heading"), "研究发现")
        lines += ["", f"## {heading}"]
        for item in section.get("items") or []:
            text = clean_str(item.get("text"))
            if not text:
                continue
            pattern_ids = [str(x) for x in item.get("pattern_ids") or []]
            ids = list(item.get("evidence_ids") or [])
            status = clean_str(item.get("status"), "supported")
            refs = [*pattern_ids, *ids]
            suffix = f" [{', '.join(refs)}]" if refs else ""
            lines.append(f"- [{status}] {text}{suffix}")
    strategies = draft.get("strategies") or []
    if strategies:
        lines += ["", "## 候选方案"]
        for s in strategies:
            title = clean_str(s.get("title"), "候选")
            lines.append("")
            lines.append(f"### {title} [{clean_str(s.get('status'), 'hypothesis')}]")
            for key, label in (
                    ("target", "目标"), ("creative_operation", "创造操作"),
                    ("novelty_source", "创新来源"), ("rationale", "理由"),
                    ("validation_plan", "验证计划")):
                value = clean_str(s.get(key))
                if value:
                    lines.append(f"- {label}: {value}")
            comps = [str(x) for x in s.get("components") or []]
            if comps:
                lines.append(f"- 组件: {'; '.join(comps)}")
            refs = [str(x) for x in s.get("pattern_ids") or []]
            refs += [str(x) for x in s.get("evidence_ids") or []]
            if refs:
                lines.append(f"- 依据: {', '.join(refs)}")
    return "\n".join(lines)


def normalize_draft(data: dict[str, Any] | None,
                    plan: dict[str, Any],
                    knowledge: dict[str, Any]) -> dict[str, Any]:
    data = data or {}
    title = clean_str(data.get("title"), clean_str(plan.get("goal"), "研究发现"))
    summary = data.get("summary") or {}
    sections = []
    for sec in data.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        items = []
        for item in sec.get("items") or []:
            if not isinstance(item, dict) or not clean_str(item.get("text")):
                continue
            items.append({
                "text": clean_str(item["text"]),
                "pattern_ids": [str(x) for x in item.get("pattern_ids") or []],
                "evidence_ids": [str(x) for x in item.get("evidence_ids") or []],
                "status": clean_str(item.get("status"), "supported"),
            })
        sections.append({
            "heading": clean_str(sec.get("heading"), "研究发现"),
            "items": items,
        })
    strategies = []
    for s in data.get("strategies") or []:
        if not isinstance(s, dict):
            continue
        strategies.append({
            "id": clean_str(s.get("id")),
            "title": clean_str(s.get("title")),
            "target": clean_str(s.get("target")),
            "components": [str(x) for x in s.get("components") or []],
            "creative_operation": clean_str(s.get("creative_operation")),
            "novelty_source": clean_str(s.get("novelty_source")),
            "rationale": clean_str(s.get("rationale")),
            "pattern_ids": [str(x) for x in s.get("pattern_ids") or []],
            "evidence_ids": [str(x) for x in s.get("evidence_ids") or []],
            "status": clean_str(s.get("status"), "hypothesis"),
            "risks": [str(x) for x in s.get("risks") or []],
            "validation_plan": clean_str(s.get("validation_plan")),
        })
    draft = {
        "title": title,
        "summary": {
            "text": clean_str(summary.get("text")),
            "pattern_ids": [str(x) for x in summary.get("pattern_ids") or []],
            "evidence_ids": [str(x) for x in summary.get("evidence_ids") or []],
        },
        "sections": sections,
        "strategies": strategies,
        "_knowledge_stats": {
            "patterns": len(knowledge.get("patterns") or []),
            "evidence": len(knowledge.get("evidence") or []),
        },
    }
    draft["markdown"] = render_draft(draft)
    return draft


def deterministic_strategies(plan: dict[str, Any],
                             knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    """无模型时从设计素材中生成候选；真实模型应按 prompt 生成更丰富候选。"""
    contract = plan.get("creative_contract")
    if not contract:
        return []
    patterns = knowledge.get("patterns") or []
    evidence = knowledge.get("evidence") or []
    ops = contract.get("creative_operations") or ["组合已有方案"]
    min_n = max(1, int(contract.get("min_candidates") or 3))
    out: list[dict[str, Any]] = []
    for i, op in enumerate(ops[:min_n]):
        candidates = [patterns[(i * 2) % len(patterns)]]
        if len(patterns) > 1:
            candidates.append(patterns[(i * 2 + 1) % len(patterns)])
        pattern_ids = [p["pattern_id"] for p in candidates]
        evidence_ids = []
        for p in candidates:
            evidence_ids.extend(p.get("evidence_ids") or [])
        source_names = [p.get("source_name") for p in candidates]
        target_names = [p.get("target_name") for p in candidates]
        out.append({
            "id": f"S-{i + 1:02d}",
            "title": f"{op}候选 {i + 1}",
            "target": clean_str(contract.get("objective"), "新方案"),
            "components": list(dict.fromkeys(source_names + target_names)),
            "creative_operation": op,
            "novelty_source": f"通过“{op}”生成，而不是对单一模式做同义复述",
            "rationale": f"组合 {len(candidates)} 条模式中的组件，"
                         "各组件分别有证据支撑，整体组合需验证",
            "pattern_ids": pattern_ids,
            "evidence_ids": list(dict.fromkeys(evidence_ids))[:20],
            "status": "hypothesis",
            "risks": ["组合后的整体行为尚无直接证据"],
            "validation_plan": "按候选方案设计可执行验证实验或原型",
        })
    return out


def deterministic_draft(plan: dict[str, Any],
                        knowledge: dict[str, Any]) -> dict[str, Any]:
    """无模型时的确定性成稿：按模式卡转写，不新增论点。"""
    patterns = knowledge.get("patterns") or []
    title = clean_str(plan.get("goal"), "研究发现")
    items: list[dict[str, Any]] = []
    for p in patterns[:60]:
        if p.get("relation_type") == "related_to":
            status = "hypothesis"
        elif p.get("evidence_tier") in ("review", "commentary", "unclassified"):
            status = "hypothesis"
        else:
            status = "supported"
        items.append({
            "text": (
                f"{p.get('source_name', '')} 通过 "
                f"{p.get('relation_type', '')} 作用于 "
                f"{p.get('target_name', '')}，"
                f"支持来源 {p.get('support_count', 0)} 篇。"
            ),
            "pattern_ids": [p["pattern_id"]],
            "evidence_ids": p.get("evidence_ids") or [],
            "status": status,
        })
    sections = [{
        "heading": "由高可信本体证据归纳的主要模式",
        "items": items,
    }]
    summary_text = (
        f"基于 {len(patterns)} 个模式卡和 "
        f"{len(knowledge.get('evidence') or [])} 条可溯源证据生成。"
    )
    strategies = deterministic_strategies(plan, knowledge)
    draft_data = {
        "title": title,
        "summary": {"text": summary_text},
        "sections": sections,
    }
    if strategies:
        draft_data["strategies"] = strategies
    return normalize_draft(draft_data, plan, knowledge)


def _compact_knowledge(knowledge: dict[str, Any],
                       max_patterns: int = 40,
                       max_evidence: int = 60) -> dict[str, Any]:
    patterns = knowledge.get("patterns") or []
    evidence = knowledge.get("evidence") or []
    design_context = None
    if knowledge.get("design_context"):
        design_context = dict(knowledge["design_context"])
        design_context["candidate_components"] = (
            design_context.get("candidate_components") or [])[:30]
    return {
        "corpus": knowledge.get("corpus") or {},
        "patterns": patterns[:max_patterns],
        "evidence": evidence[:max_evidence],
        "coverage_score": knowledge.get("coverage_score", 0),
        "design_context": design_context,
    }


def make_content_node(model=None,
                      conn: sqlite3.Connection | None = None,
                      settings: Settings | None = None):
    """构造 LangGraph 内容形成节点。model 为 None 时做确定性转写。"""
    settings = settings or default_settings

    def content_node(state: dict) -> dict:
        plan = state.get("plan") or {}
        knowledge = state.get("knowledge") or {}
        run_id = state.get("run_id")
        log_study_event(conn, settings, "content_builder", run_id, "running")
        draft = None
        model_error = None
        if model is not None:
            prompt = CONTENT_PROMPT.replace(
                "{plan}", json.dumps(plan, ensure_ascii=False, indent=2)
            ).replace(
                "{knowledge}",
                json.dumps(_compact_knowledge(knowledge),
                           ensure_ascii=False, indent=2),
            )
            if plan.get("task_kind") == "generative":
                min_n = ((plan.get("creative_contract") or {}).get(
                    "min_candidates") or 3)
                prompt += GENERATIVE_BLOCK.replace(
                    "{min_candidates}", str(min_n))
            try:
                msg = model.invoke([HumanMessage(content=prompt)])
                raw = getattr(msg, "content", str(msg))
                parsed = parse_json_object(raw)
                draft = normalize_draft(parsed, plan, knowledge)
            except Exception as exc:  # noqa: BLE001
                logger.warning("内容节点 LLM 调用失败，回退确定性转写: %s", exc)
                model_error = str(exc)
        if draft is None:
            draft = deterministic_draft(plan, knowledge)
        if model_error:
            draft["model_error"] = model_error
        log_study_event(
            conn, settings, "content_builder", run_id, "done",
            {
                "title": draft.get("title"),
                "sections": len(draft.get("sections") or []),
                "strategies": len(draft.get("strategies") or []),
                "markdown_chars": len(draft.get("markdown") or ""),
            })
        return {"draft": draft, "status": "drafted"}

    return content_node
