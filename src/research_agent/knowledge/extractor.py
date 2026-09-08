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
    "你是科研知识抽取与本体构建引擎。你的任务是从单篇论文中抽取结构化事实，"
    "输出可合并进统一科研知识图谱的 JSON。\n"
    "核心原则：\n"
    "- 只抽取文中明确陈述或直接可推断的内容，禁止臆造、补全或泛化。\n"
    "- 优先复用库中已有规范实体（运行时提供），确保同一概念在不同文献中使用相同规范名，"
    "避免重复创建。\n"
    "- 抽取粒度应足够细，能支持后续推理（如方法-材料-性能-应用之间的关联），"
    "而非仅概括主题。\n"
    "- 同时识别并抽取论文中的关键事件（如实验、发现、临床试验），"
    "它们可能表达重要的过程性知识。"
)

ENTITY_TYPES = (
    "Method, Material, Device, Drug, Disease, Model, Metric, Dataset, Task, Theory, "
    "Parameter, Property, Application, Organism, CellLine, Chemical, Target, "
    "BiologicalProcess, Technology, Tool, Standard, Regulation, Institution, Researcher"
)

SCHEMA_HINT = """请严格输出一个 JSON 对象（不要输出其它文字、不要 markdown 代码块），结构如下：
{
  "entities": [
    {
      "type": "受控类型；从下方类型列表选择，若确有必要才新造英文 CamelCase",
      "name": "规范名：优先复用库中已有规范名；否则使用该领域最标准、无歧义的写法",
      "aliases": ["该实体在文中出现的其它写法/缩写，如 RAG、additive manufacturing"],
      "attributes": {"属性名": "值", "属性名2": "值2"},
      "confidence": 0.0,
      "evidence": "支撑该实体的原句（可截断，≤300字符）"
    }
  ],
  "relations": [
    {
      "type": "受控词表中的关系词（必须按同义归一规则选择）",
      "subject": "entities.name 或库中已有规范名（必须与 entities 列表中 name 完全一致）",
      "predicate": "一句话补述，不要与 type 重复表达同一动词，例如 type=uses 时 predicate 可为 '用于合成骨支架'",
      "object": "entities.name 或库中已有规范名（必须与 entities 列表中 name 完全一致）",
      "confidence": 0.0,
      "evidence": "支撑原句（可截断，≤300字符）"
    }
  ],
  "events": [
    {
      "type": "Experiment|Study|Discovery|ClinicalTrial|Observation",
      "trigger": "精简名词短语（如 'histological analysis of group A'），不要整句、不要以报告语开头",
      "participants": ["直接参与该事件的关键实体(≤5个)，必须与 entities 列表中的 name 完全一致"],
      "time": "时间描述或 null",
      "attributes": {},
      "confidence": 0.0,
      "evidence": "支撑原句（可截断，≤300字符）"
    }
  ]
}

实体类型建议列表（优先选择，若都不匹配再自造）：
Method, Material, Device, Drug, Disease, Model, Metric, Dataset, Task, Theory,
Parameter, Property, Application, Organism, CellLine, Chemical, Target,
BiologicalProcess, Technology, Tool, Standard, Regulation, Institution, Researcher

硬性要求：
1. 连通性：每条 relation 的 subject 和 object，以及每个 event 的 participants，
   必须与 entities 列表中的 name 或「库中已有规范名」字符串完全相同（包括大小写和空格）。
   不要使用变体或缩写。
2. 每个实体尽量至少出现在一条 relation 或 event 中；确实无法关联的才作为孤立实体输出。
3. 同一概念合并：若论文中的某个概念与库中已有规范名是同一事物（包括其别名、缩写），
   则 name 必须直接复用库中规范名，并将本文中的写法加入 aliases 数组。例如库中已有
   "Method: Retrieval-Augmented Generation"，论文中写 "RAG"，则 name 应为
   "Retrieval-Augmented Generation"，aliases 包含 "RAG"。
4. 实体命名：优先使用领域通用、无歧义的标准名称；缩写需在 name 或 aliases 中给出全称。
   例如 name 可为 "Poly(lactic-co-glycolic acid)"，aliases 含 "PLGA"。
5. 置信度标尺：0.9+ 多句/多段交叉印证；0.75~0.89 原文单句直接支持；0.6~0.74 由上下文明确推断；
   <0.6 存疑尽量不输出。
6. evidence 必须引用原文句子（可节选），不得改写或总结，长度≤300字符。
7. 若论文中未出现事件，可省略 events 数组或输出空数组；但不要强行创造事件。
8. 实体命名必须是名词性领域术语/专名。禁止把句子、衔接语、证据句或报告性短语当作
   name（如 "These results suggest ...", "The histological analysis showed ...",
   "In this study, we ...", "This review summarizes ...", "We demonstrated ..."）。
   这类内容属于 relation/event 的 evidence 或 predicate，而不是实体；实体应只保留
   被陈述的核心事物名词，例如 "Calcium phosphate cement" 而非
   "The calcium phosphate cement was found to promote ..."。
9. 细节保留、禁止过度合并：仅当两个名称指向“同一个具体事物”时才复用规范名。
   带实质性修饰的不同对象必须分别建实体，并把组成/配比/掺杂/工艺写入 attributes；
   例如 "Magnesium-doped calcium phosphate cement"、"Strontium-doped calcium
   phosphate cement" 与 泛称 "Calcium phosphate cement" 是不同实体；
   禁止为了复用规范名而把不同配方、掺杂、比例或变体并入同一通用节点。
10. 关系语义要具体：能用具体关系（uses/evaluates/made_of/promotes/inhibits/
    releases/differentiates_into/regulates/activates 等）就不要退回笼统的
    related_to/causes；related_to 仅在确无更具体关系时作兜底。
11. 事件必须是“做了什么的实验/过程/发现”，而不是一句话结论：trigger 用精简名词短语
    （如 "histological analysis of group A"），禁止把整句或报告语（如
    "We demonstrate ...", "results showed ...", "was developed using ..."）作为事件；
    这类“结论性陈述”应表达为 relation/event 的 evidence，而不是事件本身。
12. involves 关系克制使用：event.participants 仅列直接参与该事件的关键实体（≤5 个），
    仅在确有参与关系时给出；不要把同句共现的无关概念全部拉成 participants，
    避免 involves 变成笼统的“共现”关系。"""


REPORTING_PHRASE_PREFIXES = [
    "these results", "these findings", "these data", "these observations",
    "our results", "our data", "our findings", "our observations",
    "the results", "the findings", "the data", "the observation",
    "the histological", "histological analysis", "immunohistochemical",
    "the present study", "this study", "this paper", "this review", "this framework",
    "in this study", "we found", "we observed", "we demonstrated", "we show",
    "we demonstrate", "here, we demonstrate", "we have demonstrated",
    "we described", "we summarize", "we propose", "it was found",
    "analysis showed", "analysis revealed", "results showed", "results demonstrated",
    "results indicated", "findings showed", "findings suggest", "findings demonstrate",
    "data showed", "data revealed", "taken together", "collectively",
    "the aim of", "the goal of",
]
_REPORT_VERBS = re.compile(
    r"\b(show(s|ed)?|suggest(s|ed)?|indicat(es|ed)?|demonstrat(es|ed)?|reveal(s|ed)?|"
    r"found|observed|describe(s|d)?|summariz(e|es|ed)?|propose(s|d)?|highlight(s|ed)?|"
    r"aim(s|ed)?|was found|were found|were developed|was investigated)\b",
    re.IGNORECASE,
)


def is_reporting_phrase(name: str | None) -> bool:
    """判断实体名是否为“衔接语/证据句/报告性短语”（应被过滤）。"""
    t = (name or "").strip()
    if not t or len(t) < 2:
        return True
    low = t.lower()
    if any(low.startswith(p) for p in REPORTING_PHRASE_PREFIXES):
        return True
    head = " ".join(low.split()[:7])
    if _REPORT_VERBS.search(head):
        return True
    return False

RELATION_VOCAB = """关系类型必须从以下列表选择（若确无匹配才可新造 CamelCase 类型，且需在输出后解释原因，但尽量不新造）：
- uses(使用/采用)
- evaluates(评估/在…上评测)
- compares(比较)
- part_of(属于/组成部分)
- improves_upon(改进自/优于)
- based_on(基于/源自)
- causes(导致/促成/诱发；指因果)
- promotes(促进/增强/加速，如促进成骨、增强血管化)
- regulates(调控/调节)
- activates(激活)
- releases(释放/缓释，如药物/离子缓释)
- differentiates_into(分化为)
- inhibits(抑制)
- treats(治疗)
- targets(靶向/结合/作用于)
- has_property(具有属性/表现出)
- made_of(由…制成/组成)
- produced_by(由…产生/合成)
- related_to(相关/关联)
- cites(引用)
- published_in(发表于)
- authored_by(作者为)
- developed_by(由…开发)
- correlates_with(与…相关/随…变化；仅用于监测/共现，非因果)
- enables(使能/实现/支持某应用或功能)
- complicates(并发/加重某并发症)
- risk_factor_for(是…的风险因素)
- results_in(导致…结果：过程/干预 → 组织/临床结果)
- is_a(是…的一种：类型层级/上下位)

同义归一规则：以下表述必须归一到左侧词表词，禁止使用多个变体制造“假新关系”：
- employ / utilize / apply → uses
- assess / benchmark / test on / validate → evaluates
- consist of / composed of → made_of
- lead to / contribute to / trigger → causes
- promote / enhances / facilitate / accelerate / boost / induce / induced → promotes
- up-regulate / upregulate → regulates
- activate / activates → activates
- release / releases / elute / sustained release → releases
- differentiate into / differentiate to → differentiates_into
- suppress / downregulate → inhibits
- exhibit / possess / show → has_property
- derived from → based_on
- outperform / better than → improves_upon
- act on / bind / interact with → targets
- associated with / relate to → related_to
- compare with / versus → compares
- treat / cure → treats
- part of / belong to → part_of
- cite / reference → cites
- publish in / appear in → published_in
- author by / written by → authored_by
- develop / create / design → developed_by
- correlate / correlate with / track → correlates_with
- enable / allow / make possible → enables
- complicate / complication of → complicates
- risk factor for / predispose to → risk_factor_for
- result in / resulting in → results_in
- is a / is an / kind of / type of / subclass of → is_a

注意：同义归一后，type 字段必须使用左侧规范词（如 uses、evaluates、promotes），不得使用右侧原词。
predicate 字段可补充具体内容，但避免重复动词。
避免关系语义过宽：只要语义能落到某个具体词（如 promotes/inhibits/releases/differentiates_into），
就不要退回 related_to 或 causes；related_to 仅作兜底。"""


ATTRIBUTE_HINT = """属性（attributes）规范：
1. 只写有意义的值：值为 None/空串/空列表时省略该键；
2. 键名用 snake_case（如 fabrication_method、defect_site、cell_types_involved）；
3. 数值必须拆成 {"value": 数字, "unit": 单位}（如 "5 wt%" → {"value":5,"unit":"wt%"}）；
   时间用相对时长（P2D/PT16H）或写明 timepoint，不写自由长句；
4. 按类型尽量给出模板字段：
   - Material: composition/components/fabrication_method/architecture/application
   - Model: species/defect_site/model_type
   - Disease: etiology/pathology_site/related_signs
   - BiologicalProcess: regulators/cell_types_involved/downstream_outcome
   - Property: metric_type（值拆为 value+unit 或 rating）
5. 不要用“excellent/controllable”这类形容词冒充定量；形容词仅放 rating 并在 evidence 保留原文。"""


def build_prompt(paragraphs: list[str], paper_meta: dict[str, Any] | None = None,
                 existing_entities: list[str] | None = None) -> str:
    meta = paper_meta or {}
    header = (
        f"论文: {meta.get('title', '未知')} | 期刊: {meta.get('venue', '未知')} "
        f"| 年份: {meta.get('pub_year', '未知')}"
    )
    text = "\n\n".join(paragraphs)
    parts = [SYSTEM_HINT, SCHEMA_HINT, ATTRIBUTE_HINT, RELATION_VOCAB, header]
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
