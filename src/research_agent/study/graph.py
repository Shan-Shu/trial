"""LangGraph 编排：工作规划 -> 知识消费 -> 内容形成 -> 审核校对。

四节点之间不直接传论文全文或 SQL；只传 plan、knowledge bundle、draft、review。
知识消费节点在语料不足时可调用现有数据构建流水线，或停在 needs_collection。
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from uuid import uuid4
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from research_agent.config import Settings
from research_agent.db import connect, log_event
from research_agent.models import build_role_model
from research_agent.study.collection import collect_mission
from research_agent.study.consumer import make_knowledge_consumer_node
from research_agent.study.content import make_content_node
from research_agent.study.planner import make_planner_node
from research_agent.study.reviewer import make_review_node

logger = logging.getLogger(__name__)


class StudyState(TypedDict, total=False):
    request: str
    run_id: str
    plan: dict
    knowledge: dict
    collection_report: dict
    retrieval_request: dict
    draft: dict
    review: dict
    decision: str
    review_rounds: int
    edge_gaps: list
    collect_gaps: bool
    gap_retrieval_done: bool
    status: str
    error: str


@dataclass
class StudyServices:
    """四节点的模型与依赖。knowledge consumer 本身不需要模型。"""
    planner_model: Any = None
    content_model: Any = None
    review_model: Any = None
    settings: Settings = field(default_factory=Settings.from_env)
    collector: Any = None


def _route_after_consumer(state: StudyState) -> str:
    if state.get("status") == "needs_collection":
        return "needs_collection"
    return "content"


def _route_after_reviewer(state: StudyState) -> str:
    decision = state.get("decision") or "revise"
    if decision in ("manual_review",):
        return "manual_review"
    if decision == "pass":
        return "pass"
    if decision == "need_more_data":
        return "knowledge_consumer"
    return "content_builder"


def _route_after_content(state: StudyState) -> str:
    """内容给出证据缺口后，若 planner 开启证据缺口策略则先回补再审核。"""
    plan = state.get("plan") or {}
    retrieval = plan.get("retrieval") or {}
    if (state.get("edge_gaps") and not state.get("gap_retrieval_done")
            and (retrieval.get("evidence_gap_enabled")
                 or retrieval.get("strategy") == "evidence_gap")):
        return "knowledge_consumer"
    return "reviewer"


def build_study_graph(services: StudyServices | None = None,
                      conn: sqlite3.Connection | None = None,
                      max_results_override: int | None = None):
    services = services or StudyServices()
    g = StateGraph(StudyState)
    g.add_node("planner", make_planner_node(
        services.planner_model, max_results_override=max_results_override,
        conn=conn, settings=services.settings))
    g.add_node(
        "knowledge_consumer",
        make_knowledge_consumer_node(
            conn=conn, settings=services.settings, collector=services.collector),
    )
    g.add_node("content_builder", make_content_node(
        services.content_model, conn=conn, settings=services.settings))
    g.add_node(
        "reviewer",
        make_review_node(services.review_model, max_rounds=3,
                         conn=conn, settings=services.settings),
    )

    g.add_edge(START, "planner")
    g.add_edge("planner", "knowledge_consumer")
    g.add_conditional_edges(
        "knowledge_consumer",
        _route_after_consumer,
        {"content": "content_builder", "needs_collection": END},
    )
    g.add_conditional_edges(
        "content_builder",
        _route_after_content,
        {"knowledge_consumer": "knowledge_consumer", "reviewer": "reviewer"},
    )
    g.add_conditional_edges(
        "reviewer",
        _route_after_reviewer,
        {
            "pass": END,
            "content_builder": "content_builder",
            "knowledge_consumer": "knowledge_consumer",
            "manual_review": END,
        },
    )
    return g.compile()


def run_study(request: str,
              services: StudyServices | None = None,
              conn: sqlite3.Connection | None = None,
              force_collect: bool = False,
              max_results_override: int | None = None) -> dict[str, Any]:
    app = build_study_graph(services, conn,
                            max_results_override=max_results_override)
    run_id = uuid4().hex[:12]
    own = conn is None
    db = conn or connect((services or StudyServices()).settings.db_path)
    try:
        log_event(db, "study", "session-start", None,
                  {"run_id": run_id, "request": request, "status": "running"})
    finally:
        if own:
            db.close()
    out = app.invoke({
        "request": request,
        "run_id": run_id,
        "review_rounds": 0,
        "status": "started",
        "force_collect": force_collect,
    })
    own = conn is None
    db = conn or connect((services or StudyServices()).settings.db_path)
    try:
        log_event(db, "study", "session-end", None, {
            "run_id": run_id,
            "status": out.get("status"),
            "decision": out.get("decision"),
            "request": request,
        })
    finally:
        if own:
            db.close()
    return out


def _resolve_study_role(role: str, choice: str, smoke: bool) -> Any:
    if smoke or choice == "smoke":
        return None
    if choice == "none":
        return None
    provider = None if choice in ("auto", None) else choice
    try:
        return build_role_model(role, provider=provider)
    except Exception as exc:  # noqa: BLE001
        print(f"[{role}] 警告：{exc}（本次以确定性实现运行）")
        return None


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    providers = sorted({"openai", "deepseek", "qwen", "glm",
                        "anthropic", "google"})
    ap = argparse.ArgumentParser(
        description="LangGraph 四节点研究任务：规划/知识消费/内容/审核")
    ap.add_argument("--request", default="调研 3D 打印骨支架的最新研究前沿")
    ap.add_argument("--db", help="SQLite 数据库路径（默认 data/research_agent.db）")
    ap.add_argument("--planner-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--content-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--review-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--retriever-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--quality-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--knowledge-llm", choices=["auto", "none", "smoke"] + providers,
                    default="auto")
    ap.add_argument("--llm-smoke", action="store_true",
                    help="四个顶层节点均不使用真实 LLM（确定性实现）")
    ap.add_argument("--collect", action="store_true",
                    help="知识不足时调用现有 retrieval->quality->knowledge 补集")
    ap.add_argument("--max-results", type=int, default=None,
                    help="覆盖规划节点的单主题结果上限（默认使用任务单）")
    ap.add_argument(
        "--source",
        choices=["pubmed", "arxiv", "both", "europepmc",
                 "semantic_scholar", "openalex", "ncpssd", "fulltext", "all"],
        default="fulltext")
    args = ap.parse_args(argv)

    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db

    collector = None
    if args.collect:
        from research_agent.pipeline import Services as PipelineServices
        from research_agent.retrieval.api_clients import ApiHub

        pipeline_services = PipelineServices(
            api=ApiHub(source=args.source),
            retriever_model=_resolve_study_role(
                "retriever", args.retriever_llm, args.llm_smoke),
            quality_model=_resolve_study_role(
                "quality", args.quality_llm, args.llm_smoke),
            knowledge_model=_resolve_study_role(
                "knowledge", args.knowledge_llm, args.llm_smoke),
            settings=settings,
        )
        collector = lambda req: collect_mission(req, services=pipeline_services)

    services = StudyServices(
        planner_model=_resolve_study_role(
            "planner", args.planner_llm, args.llm_smoke),
        content_model=_resolve_study_role(
            "content", args.content_llm, args.llm_smoke),
        review_model=_resolve_study_role(
            "review", args.review_llm, args.llm_smoke),
        settings=settings,
        collector=collector,
    )
    out = run_study(
        args.request, services,
        force_collect=args.collect,
        max_results_override=args.max_results,
    )
    status = out.get("status")
    print(f"\n状态: {status} | 请求: {args.request}")
    plan = out.get("plan") or {}
    print(f"任务单: {plan.get('goal', '')} [{plan.get('content_type', '')}]")
    if status == "needs_collection":
        print("\n知识消费节点请求补集:")
        print((out.get("retrieval_request") or {}).get("suggested_route"))
        print("可对同库先运行 research-agent-pipeline，或加 --collect 重试")
        return 0
    knowledge = out.get("knowledge") or {}
    print(f"模式卡: {len(knowledge.get('patterns') or [])} | "
          f"证据卡: {len(knowledge.get('evidence') or [])}")
    draft = out.get("draft") or {}
    review = out.get("review") or {}
    if review.get("decision") == "pass":
        print("\n----- 审核通过内容 -----")
        print(draft.get("markdown") or "（无内容）")
    else:
        print("\n----- 审核结论 -----")
        print(review.get("summary") or "")
        for issue in review.get("issues") or []:
            print(f"- [{issue.get('severity')}] {issue.get('problem')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
