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
from research_agent.study.events import log_study_event
from research_agent.study.json_utils import clean_str, parse_json_object

logger = logging.getLogger(__name__)

PLANNER_PROMPT = """你是科研辅助系统的工作规划节点。用户会给出较简单或模糊的指令，
你需要把指令转成一份结构化“研究任务单”，供知识消费节点和内容形成节点执行。

硬性要求：
1. 不要生成内容大纲，不要预设章节，不要预判研究结论；
2. 重点是把“收集什么证据、多宽、多久之前、哪些分析维度”说清楚；
3. seed_terms 必须是英文检索词，覆盖领域核心词、方法/催化剂/机理、评价与应用词；
   禁止把用户整句话直接作为 seed_terms 或 domain；
4. content_type 从 research_report/frontier_review/research_directions/experiment_protocol 中选择；
5. 只输出 JSON 对象，不要代码块，不要解释。

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
  "domain_profile": {{
    "domain_kind": "chemistry|biomedicine|materials|general",
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
  "deliverable": {{
    "format": "markdown",
    "sections_policy": "emergent",
    "language": "zh"
  }},
  "constraints": []
}}

请直接输出可解析的 JSON：

注意：domain_profile.dimensions 必须针对领域真实需要，例如化学领域写
“催化、底物范围、区域/立体选择性、机理、产率”，不要写“适应症/临床转化”等无关维度。"""


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


def normalize_plan(data: dict[str, Any] | None, request: str) -> dict[str, Any]:
    """补全缺失字段，保证后续节点拿到统一结构。"""
    raw = data or {}
    goal = clean_str(raw.get("goal"), request)
    domain = clean_str(raw.get("domain"), goal)
    content_type = clean_str(raw.get("content_type"), infer_content_type(request))
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
    analysis = [clean_str(t) for t in raw.get("analysis_targets") or [] if clean_str(t)]
    if not analysis:
        analysis = ["方法", "材料", "性能指标", "应用", "开放问题"]
    deliverable = raw.get("deliverable") or {}
    return {
        "goal": goal,
        "domain": domain,
        "content_type": content_type,
        "domain_profile": normalize_domain_profile(
            raw.get("domain_profile"), domain, request),
        "analysis_targets": analysis,
        "mission": mission,
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
