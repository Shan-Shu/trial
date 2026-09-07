"""LLM 知识抽取：把论文文本转为结构化的 实体/关系/属性/事件 JSON。

提示词允许模型按需引入新的实体/关系类型（动态本体 schema 演化的来源）。
返回的每条知识自带 confidence（模型自评），最终入库时再与文献质量 Q 融合。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage

logger = logging.getLogger(__name__)


SYSTEM_HINT = (
    "你是科研知识抽取与本体构建引擎。目标是从论文中产出能合并进一个统一科研知识图谱、"
    "便于跨文献合并的结构化知识。只抽取文中明确陈述的内容，禁止臆造。"
)

ENTITY_TYPES = (
    "Method, Dataset, Metric, Task, Concept, Tool, Person, Organization, "
    "Material, Disease, Drug, Gene, BiologicalProcess, Event"
)

RELATION_VOCAB = """关系类型请从以下受控词表选择（如实在无匹配才可新造 CamelCase 类型）：
uses(使用/采用) · evaluates(评估/在…上评测) · compares(比较) · part_of(属于/组成部分)
· improves_upon(改进自/优于) · based_on(基于/源自) · causes(导致/促进/诱导) · inhibits(抑制)
· treats(治疗) · targets(靶向/结合/作用于) · has_property(具有属性/表现出)
· made_of(由…制成/组成) · produced_by(由…产生/合成) · related_to(相关/关联)
· cites(引用) · published_in(发表于) · authored_by(作者为) · developed_by(由…开发)

同义归一规则：以下表述必须归一到左侧词表词，禁止使用多个变体制造“假新关系”：
employ/utilize/apply → uses；assess/benchmark/test on/validate → evaluates；
consist of/composed of → made_of；lead to/promote/enhance/induce/contribute to → causes；
suppress/downregulate → inhibits；exhibit/possess/show → has_property；
derived from → based_on；outperform/better than → improves_upon；
act on/bind/interact with → targets；associated with/relate to → related_to。"""

SCHEMA_HINT = """
请严格输出一个 JSON 对象（不要输出其它文字、不要 markdown 代码块），结构如下：
{
  "entities": [
    {
      "type": "受控类型；确有必要才新造(英文 CamelCase)",
      "name": "规范名：优先复用「库中已有规范名」；否则用论文中最标准/通用的写法",
      "aliases": ["该实体在文中出现的其它写法/缩写，如 RAG、additive manufacturing 等"],
      "attributes": {"属性名": 值},
      "confidence": 见置信度标尺,
      "evidence": "支撑该实体的原句(可截断)"
    }
  ],
  "relations": [
    {
      "type": "受控词表中的关系词（按同义归一规则）",
      "subject": "entities.name 或库中已有规范名",
      "predicate": "一句话补述，不要与 type 重复表达同一动词",
      "object": "entities.name 或库中已有规范名",
      "confidence": 见置信度标尺,
      "evidence": "支撑原句"
    }
  ],
  "events": [
    {
      "type": "Experiment|Study|Discovery|ClinicalTrial|Observation",
      "trigger": "触发词或原句片段",
      "participants": ["必须是 entities.name 或库中已有规范名"],
      "time": "时间描述或 null",
      "attributes": {},
      "confidence": 见置信度标尺,
      "evidence": "支撑原句"
    }
  ]
}

硬性要求：
1. 连通性：每条 relation/event 的 subject、object、participants 必须与 entities 列表里的
   name 或「库中已有规范名」精确一致（同一字符串）；不要把同一概念用变体再写一次。
2. 每个实体尽量至少出现在一条 relation 或 event 中；确实无法关联的再作孤立实体。
3. 同一概念合并：若论文中的表述与库中已有规范名是同一事物（或其别名/缩写），
   name 必须直接复用库中规范名，并把本文写法放进 aliases，禁止重复创建。
4. 实体命名：取该领域最通用、无歧义的标准名；首字母缩写在 name 或 aliases 中给出全称。
5. 置信度标尺：0.9+ 多句/多段交叉印证；0.75~0.89 原文单句直接支持；0.6~0.74 由上下文
   明确推断；<0.6 存疑尽量不输出。
6. evidence 请引用原文句子（可节选），长度≤300 字符。"""


def build_prompt(paragraphs: list[str], paper_meta: dict[str, Any] | None = None,
                 existing_entities: list[str] | None = None) -> str:
    meta = paper_meta or {}
    header = (
        f"论文: {meta.get('title', '未知')} | 期刊: {meta.get('venue', '未知')} "
        f"| 年份: {meta.get('pub_year', '未知')}"
    )
    text = "\n\n".join(paragraphs)
    parts = [SYSTEM_HINT, SCHEMA_HINT, RELATION_VOCAB, header]
    if existing_entities:
        parts.append(
            "库中已有（尽量复用的）规范实体（Type: Name）：\n"
            + "\n".join(existing_entities[:150])
            + "\n（若本文出现同一概念，请复用其规范名并把本文写法加入 aliases）"
        )
    parts.append(f"----------------\n论文段落：\n{text}\n----------------\n输出 JSON:")
    return "\n\n".join(parts)


def parse_model_json(raw: str) -> dict[str, Any]:
    """容忍地解析模型输出为 dict：剥除代码围栏、截取首个 {…}。"""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text).strip()
    text = re.sub(r"\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出中未找到 JSON 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON 根节点不是对象")
    data.setdefault("entities", [])
    data.setdefault("relations", [])
    data.setdefault("events", [])
    return data


def _conf(value: Any) -> float:
    try:
        c = float(value)
    except (TypeError, ValueError):
        return 0.5
    return max(0.0, min(1.0, c))


def blend_confidence(model_conf: float, quality_q: float | None,
                     flagged: bool, settings=None) -> float:
    """融合置信度 = 0.6*模型自评 + 0.4*文献质量权重。

    文献质量权重 = Q（若为“标记后发送”，按 q_flag_penalty 折扣），体现
    “根据质量评估结果和文本逻辑给出置信度”。
    """
    settings = settings
    if settings is None:
        from research_agent.config import settings as _s
        settings = _s
    q = quality_q if quality_q is not None else 0.5
    qw = q * (settings.q_flag_penalty if flagged else 1.0)
    return round(max(0.0, min(1.0, 0.6 * model_conf + 0.4 * qw)), 3)


class KnowledgeExtractor:
    """按文本块调用模型并解析结构化结果。model 需有 invoke([HumanMessage])。"""

    def __init__(self, model, settings=None) -> None:
        self.model = model
        if settings is None:
            from research_agent.config import settings as _s
            settings = _s
        self.settings = settings

    def extract(self, paragraphs: list[str],
                paper_meta: dict[str, Any] | None = None,
                existing_entities: list[str] | None = None) -> dict[str, Any]:
        prompt = build_prompt(paragraphs, paper_meta, existing_entities)
        try:
            msg = self.model.invoke([HumanMessage(content=prompt)])
            raw = msg.content if isinstance(msg, AIMessage) else str(getattr(msg, "content", msg))
            return parse_model_json(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 抽取失败: %s", exc)
            return {"entities": [], "relations": [], "events": [], "error": str(exc)}
