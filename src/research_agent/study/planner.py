"""工作规划节点。

职责边界：只把用户的模糊请求规范化为“语料采集任务”和内容交付要求，
不在这一阶段预设综述章节或研究结论。真正的章节结构由内容形成节点在
获得知识消费结果后按数据形态自然生成。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import date
from typing import Any

from langchain_core.messages import HumanMessage

from research_agent.config import Settings, settings as default_settings
from research_agent.domains import normalize_domain_profile
from research_agent.retrieval.skills import (
    normalize_retrieval,
)
from research_agent.study.events import log_study_event
from research_agent.study.json_utils import clean_str, parse_json_object

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """你是科研辅助系统的工作规划节点。用户会给出较简单或模糊的指令，
你需要把指令转成一份结构化“研究任务单”，供知识消费节点和内容形成节点执行。

硬性要求：
1. 不要生成内容大纲，不要预设章节，不要预判研究结论；
2. 先判断任务性质：summary(综述/调研)、generative(提出新方法/新方案/新设计)、
   frontier(前沿探索)、evaluation(评估/比较/选择)；
3. 对 generative 任务，必须输出 creative_contract，说明需要生成什么、
   可以组合哪些方向、最少生成几个候选、如何判断“不是简单复述”；
4. 再把“收集什么证据、多宽、多久之前、哪些分析维度”说清楚；
5. 领域画像可随任务生成，但任务性质和生成要求必须是领域无关的；
6. seed_terms 使用能直接投递到目标文献库的检索词：国际学术库用英文，
   NCPSSD/CNKI 等中文库用中文；禁止把用户整句话直接作为 seed_terms 或 domain；
7. content_type 从 research_report/frontier_review/research_directions/experiment_protocol 中选择；
8. 根据用户需求判断检索策略 retrieval：
   - 默认 broad：先做广泛主题检索；
   - 若用户要求对本体已有边补强、多源验证、共识或证据缺口，启用
     evidence_gap（evidence_gap_enabled=true）；
   - 仅当用户明确要求“某一单领域的精深挖掘、系统追溯、参考文献/引用溯源”时，
     才启用 deep_single_domain；不得对普通综述自动启用递归溯源。
9. 只输出 JSON 对象，不要代码块，不要解释。

示例（只参考字段风格，不要照抄用户原话作为 domain/seed_terms）：
用户原话：尝试提出一种炔酰胺构建多元氮杂化合物的新方法
合理 seed_terms 示例：["ynamide annulation", "ynamide nitrogen heterocycle synthesis",
"alkynyl amide cyclization", "ynamide catalytic cycloaddition"]

当前日期：{today}

用户原话：
{request}

输出 JSON 结构：
{{
  "goal": "一句话目标",
  "domain": "研究领域",
  "content_type": "research_report|frontier_review|research_directions|experiment_protocol",
  "task_kind": "summary|generative|frontier|evaluation",
  "creative_contract": {{
    "objective": "用户期望获得的新对象/新方案描述",
    "focus": "研究或设计焦点",
    "min_candidates": 3,
    "creative_operations": ["组合已有方案", "跨域迁移", "替换组件", "扩展对象范围"],
    "constraints": ["不能只复述已有方案", "必须区分假设与已知事实"],
    "evaluation_criteria": ["新颖性", "可行性", "可解释性", "可验证性"]
  }},
  "domain_profile": {{
    "domain_kind": "chemistry|biomedicine|materials|humanities_social_science|general",
    "dimensions": ["该领域应覆盖的检索/分析维度"],
    "candidate_entity_types": ["首轮可试用的实体类型"],
    "candidate_relation_types": ["首轮可试用的关系类型"],
    "schema_status": "candidate"
  }},
  "analysis_targets": ["方法", "材料", "性能指标", "应用", "开放问题"],
  "mission": {{
    "seed_terms": ["英文检索词1", "英文检索词2", "英文检索词3"],
    "max_results": 80,
    "min_confidence": 0.6,
    "collection_mode": "broad",
    "recency_window": "2018-01-01:{today}"
  }},
  "retrieval": {{
    "strategy": "broad|evidence_gap|deep_single_domain",
    "evidence_gap_enabled": false,
    "deep_single_domain_enabled": false,
    "min_support_target": 2,
    "max_skill_rounds": 2,
    "relevance_gate": "strict",
    "reference_direction": "both"
  }},
  "deliverable": {{
    "format": "markdown",
    "sections_policy": "emergent",
    "language": "zh"
  }},
  "constraints": []
}}

请直接输出可解析的 JSON：

注意：creative_contract 必须用领域无关语言描述“生成什么、如何生成、如何评价”，
domain_profile 才用来实例化领域词汇。"""


def infer_content_type(request: str) -> str:
    text = request.lower()
    if any(k in text for k in ("提出", "propose", "new method", "新方法",
                               "合成方法", "strategy", "策略")):
        return "research_directions"
    if any(k in text for k in ("实验", "protocol", "design", "设计")):
        return "experiment_protocol"
    if any(k in text for k in ("方向", "idea", "gap", "候选")):
        return "research_directions"
    if any(k in text for k in ("前沿", "最新", "进展", "survey", "review")):
        return "frontier_review"
    return "research_report"


GENERATIVE_HINTS = (
    "提出", "propose", "new method", "新方法", "新方案", "设计", "框架",
    "new framework", "新框架", "候选", "策略", "approach",
)
SUMMARY_HINTS = ("综述", "总结", "review", "summarize", "调研")
FRONTIER_HINTS = ("前沿", "最新", "进展", "趋势", "frontier")
EVALUATION_HINTS = ("评估", "比较", "对比", "选择", "evaluate", "compare")


def infer_task_kind(request: str) -> str:
    text = request.lower()
    if any(k in text for k in GENERATIVE_HINTS):
        return "generative"
    if any(k in text for k in EVALUATION_HINTS):
        return "evaluation"
    if any(k in text for k in FRONTIER_HINTS):
        return "frontier"
    if any(k in text for k in SUMMARY_HINTS):
        return "summary"
    return "generative"


def normalize_creative_contract(data: dict[str, Any] | None,
                                request: str,
                                domain: str) -> dict[str, Any]:
    """领域无关的生成任务契约：说明生成什么、如何生成、如何评价。"""
    raw = data or {}
    objective = clean_str(raw.get("objective"), request)
    focus = clean_str(raw.get("focus"), domain or objective)
    operations = [
        clean_str(x) for x in raw.get("creative_operations") or []
        if clean_str(x)
    ]
    if not operations:
        operations = [
            "组合已有方案/方法",
            "把已有方法迁移到新的对象或场景",
            "替换或改造成分/组件/条件",
            "扩展原有方案到更一般情形",
            "设计新的顺序或流水线",
        ]
    criteria = [
        clean_str(x) for x in raw.get("evaluation_criteria") or []
        if clean_str(x)
    ]
    if not criteria:
        criteria = ["新颖性", "可行性", "可解释性", "可验证性"]
    constraints = [
        clean_str(x) for x in raw.get("constraints") or []
        if clean_str(x)
    ]
    if not constraints:
        constraints = [
            "不能只复述已有方案",
            "组合的每一部分应可追溯",
            "整体候选应标记为待验证假设",
        ]
    return {
        "objective": objective,
        "focus": focus,
        "min_candidates": max(1, int(raw.get("min_candidates") or 3)),
        "creative_operations": operations,
        "constraints": constraints,
        "evaluation_criteria": criteria,
    }


def normalize_plan(data: dict[str, Any] | None, request: str) -> dict[str, Any]:
    """补全缺失字段，保证后续节点拿到统一结构。"""
    raw = data or {}
    goal = clean_str(raw.get("goal"), request)
    domain = clean_str(raw.get("domain"), goal)
    content_type = clean_str(raw.get("content_type"), infer_content_type(request))
    task_kind = clean_str(raw.get("task_kind"), infer_task_kind(request)).lower()
    if task_kind not in ("summary", "generative", "frontier", "evaluation"):
        task_kind = infer_task_kind(request)
    mission_raw = raw.get("mission") or {}
    terms = [clean_str(t) for t in mission_raw.get("seed_terms") or [] if clean_str(t)]
    if not terms:
        terms = [domain]
    today = date.today().isoformat()
    mission = {
        "seed_terms": terms,
        "max_results": int(mission_raw.get("max_results") or 80),
        "min_confidence": float(mission_raw.get("min_confidence") or 0.6),
        "collection_mode": clean_str(mission_raw.get("collection_mode"), "broad"),
        "recency_window": clean_str(
            mission_raw.get("recency_window"), f"2018-01-01:{today}"),
    }
    retrieval = normalize_retrieval(raw.get("retrieval"), request)
    analysis = [clean_str(t) for t in raw.get("analysis_targets") or [] if clean_str(t)]
    if not analysis:
        analysis = ["方法", "材料", "性能指标", "应用", "开放问题"]
    deliverable = raw.get("deliverable") or {}
    creative_contract = None
    if task_kind == "generative":
        creative_contract = normalize_creative_contract(
            raw.get("creative_contract"), request, domain)
    return {
        "goal": goal,
        "domain": domain,
        "content_type": content_type,
        "task_kind": task_kind,
        "creative_contract": creative_contract,
        "domain_profile": normalize_domain_profile(
            raw.get("domain_profile"), domain, request),
        "analysis_targets": analysis,
        "mission": mission,
        "retrieval": retrieval,
        "deliverable": {
            "format": clean_str(deliverable.get("format"), "markdown"),
            "sections_policy": "emergent",
            "language": clean_str(deliverable.get("language"), "zh"),
        },
        "constraints": raw.get("constraints") or [],
    }


def deterministic_plan(request: str) -> dict[str, Any]:
    """无模型或模型解析失败时生成可运行的任务单。"""
    return normalize_plan(None, request)


def make_planner_node(model=None,
                      max_results_override: int | None = None,
                      conn: sqlite3.Connection | None = None,
                      settings: Settings | None = None):
    """构造 LangGraph 工作规划节点。model 为 None 时使用确定性任务单。"""
    settings = settings or default_settings

    def planner_node(state: dict) -> dict:
        request = clean_str(state.get("request"), "请检索并整理研究前沿")
        run_id = state.get("run_id")
        log_study_event(conn, settings, "planner", run_id, "running",
                        {"request": request[:500]})
        plan = None
        model_error = None
        if model is not None:
            prompt = PLANNER_PROMPT.replace(
                "{today}", date.today().isoformat()
            ).replace("{request}", request)
            try:
                msg = model.invoke([HumanMessage(content=prompt)])
                raw = getattr(msg, "content", str(msg))
                parsed = parse_json_object(raw)
                plan = normalize_plan(parsed, request)
            except Exception as exc:  # noqa: BLE001
                logger.warning("规划节点 LLM 调用失败，回退确定性任务单: %s", exc)
                model_error = str(exc)
        if plan is None:
            plan = deterministic_plan(request)
        if model_error:
            plan["model_error"] = model_error
        if max_results_override is not None:
            plan["mission"]["max_results"] = max(1, int(max_results_override))
        log_study_event(
            conn, settings, "planner", run_id, "done",
            {
                "goal": plan.get("goal"),
                "domain": plan.get("domain"),
                "content_type": plan.get("content_type"),
                "task_kind": plan.get("task_kind"),
                "min_candidates": ((plan.get("creative_contract") or {})
                                   .get("min_candidates")),
                "seed_terms": plan.get("mission", {}).get("seed_terms"),
                "max_results": plan.get("mission", {}).get("max_results"),
            })
        return {"plan": plan, "status": "planned"}

    return planner_node


def dump_plan(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False, indent=2)


_WORDISH = re.compile(r"[a-zA-Z0-9]{4,}|[\u4e00-\u9fff]{2,}")


def split_seed_terms(request: str) -> list[str]:
    """供外部工具层使用的轻量分词，仅作为确定性检索兜底。"""
    return list(dict.fromkeys(m.group(0) for m in _WORDISH.finditer(request)))[:8]
