"""质量评估节点 LLM（GLM 4.7 Flash）。

GLM 依据文献元数据给出各权威性子项评分与学科速度判断，A/T/Q 仍由代码按
既定公式计算与路由，保证规则可复现：
    A = 0.5*venue_factor + 0.3*h_factor + 0.2*citation_factor
    T = f(出版年份, 学科半衰期)
    Q = 0.6*A + 0.4*T
模型缺失或输出不可解析时回退确定性评分。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


def assess_with_llm(rec: dict[str, Any], model) -> dict[str, Any] | None:
    """返回 LLM 评分子项；失败返回 None（由调用方回退）。"""
    authors = rec.get("authors") or []
    payload = {
        "title": rec.get("title"),
        "venue": rec.get("venue"),
        "venue_issn": rec.get("venue_issn"),
        "source_type": rec.get("source_type"),
        "pub_year": rec.get("pub_year"),
        "citation_count": rec.get("citation_count"),
        "avg_h_index": rec.get("avg_h_index"),
        "author_h_indices": [a.get("h_index") for a in authors if a.get("h_index")],
        "author_count": len(authors),
    }
    prompt = (
        "你是科研文献质量评估专家。请基于下面的文献元数据 JSON，仅输出一个 JSON 对象"
        "（不要多余文字），字段与取值要求如下：\n"
        "{\n"
        '  "venue_quartile": "JCR/SCI 分区，Q1-Q4；无法确定填 null",\n'
        '  "venue_factor": 0-1 小数（期刊/出版社权威性，预印本给 0.5 左右）, \n'
        '  "h_factor": 0-1 小数（作者团队学术影响力，结合 H 指数与数量）, \n'
        '  "citation_factor": 0-1 小数（被引情况，按年代与领域综合）, \n'
        '  "field_velocity": "fast|medium|slow"（该文所属学科前沿迭代速度）, \n'
        '  "venue_note": "一句话说明分区判断依据",\n'
        '  "rationale": "两句话以内的评估理由"\n'
        "}\n"
        "规则：子项均在 0-1；不要自行计算 A/T/Q，最终评分由公式完成。\n"
        "评分标尺（供对齐，不必机械照搬）：\n"
        "- venue_factor：0.90-0.95 顶刊/顶会/学会旗舰；0.78-0.88 领域主流 Q1；"
        "0.65-0.75 一般 Q2/中等会议；0.50-0.60 预印本/未知来源；\n"
        "- h_factor：0.9≈H≥60 的资深团队，0.7≈H 20-40，0.5≈H 5-15，"
        "0.4≈新团队或信息缺失；可结合团队规模微调；\n"
        "- citation_factor：按该领域同年份论文的相对被引位置判断，"
        "前 1%≈0.95、前 10%≈0.8、前 50%≈0.6、接近 0≈0.3；\n"
        "- field_velocity：AI/CS≈fast，生物医药/材料/化学≈medium，数学/基础理论≈slow。\n"
        "文献元数据：" + json.dumps(payload, ensure_ascii=False)
    )
    try:
        msg = model.invoke([HumanMessage(content=prompt)])
        raw = getattr(msg, "content", str(msg))
        text = re.sub(r"^```(?:json)?\s*", "", str(raw).strip())
        text = re.sub(r"\s*```$", "", text).strip()
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            raise ValueError("未找到 JSON")
        data = json.loads(text[s:e + 1])
        if not isinstance(data, dict):
            raise ValueError("非对象")
        return data
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 质量评估失败，回退规则评分: %s", exc)
        return None
