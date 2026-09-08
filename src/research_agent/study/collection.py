"""研究语料补集服务：把检索请求接到现有 pipeline。

现有 retrieval -> quality -> knowledge 流水线已承担“语料建库”职责；这里只做
批量调度，不新增 LLM 角色。无网络/无 Key 时可传入离线 Services 完成冒烟验证。
"""
from __future__ import annotations

import logging
from typing import Any

from research_agent.study.json_utils import clean_str

logger = logging.getLogger(__name__)


def collect_mission(request: dict[str, Any],
                    services=None,
                    max_topics: int = 8,
                    per_topic: int | None = None) -> dict[str, Any]:
    """调用现有 pipeline.run_topic 按 seed_terms 补充本地语料和动态本体。"""
    from research_agent.pipeline import run_topic

    terms = [clean_str(t) for t in request.get("seed_terms") or [] if clean_str(t)]
    if not terms:
        return {"count": 0, "errors": ["seed_terms 为空"]}
    max_results = max(1, int(request.get("max_results") or 80))
    per = per_topic or max(1, min(10, max_results // len(terms)))
    collected: list[str] = []
    errors: list[dict[str, Any]] = []
    for term in terms[:max_topics]:
        try:
            out = run_topic(term, max_results=per, services=services)
            keys = (out.get("ingest") or {}).get("paper_keys") or []
            collected.extend(keys)
            logger.info("collect topic=%s papers=%d", term, len(keys))
        except Exception as exc:  # noqa: BLE001
            logger.warning("collect topic %s failed: %s", term, exc)
            errors.append({"term": term, "error": str(exc)})
    return {
        "count": len(collected),
        "paper_keys": collected,
        "errors": errors,
    }
