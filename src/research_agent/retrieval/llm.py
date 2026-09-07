"""检索节点 LLM（DeepSeek V4）：查询规划 + 元数据规整。

确定性 API（arXiv/OpenAlex/Crossref）负责真正的检索与下载，
LLM 负责「动脑」的部分：
1. plan_queries：把用户研究主题拆解为多条更精准的检索式；
2. clean_metadata：把多源原始元数据规整为规范字段，并尽力补全
   作者/单位/发表情况/DOI（供质量节点校验，缺漏再回补）。

模型缺失或输出不可解析时，调用方回退到原确定性实现，不影响流程。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


def _parse_first_json(text: str) -> Any | None:
    """容忍解析模型输出中的首个 JSON 对象/数组。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t).strip()
    t = re.sub(r"\s*```$", "", t).strip()
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        s, e = t.find(open_ch), t.rfind(close_ch)
        if s != -1 and e > s:
            try:
                return json.loads(t[s:e + 1])
            except json.JSONDecodeError:
                continue
    return None


PLAN_PROMPT_TEMPLATE = """你是科研文献检索规划器。用户会给出一个简短的研究主题（可能只有几个词，如“新型骨修复生物材料”）。你需要基于该主题，生成一组英文检索式，每个检索式聚焦一个不同的研究子领域或维度，以便系统逐个执行检索（轮询），全面获取相关文献。

你需要考虑以下子领域维度（根据主题自动选择相关项，尽可能覆盖更多，但每条检索式只聚焦1-2个维度，避免混合过多概念导致结果不相关）：
- 核心关键词：主题本身、同义词、近义词、上下位概念。
- 方法与技术：涉及的主要方法、技术、模型、工具、工艺。
- 应用与场景：主要应用领域、适应症、使用场景、目标对象。
- 机理与原理：作用机制、原理、理论、结构-功能关系。
- 性能与特性：活性、稳定性、生物相容性、力学性能、耐久性等（根据主题调整性能指标）。
- 变种与拓展：不同材料类型、改进型、衍生技术、复合体系。
- 优化与调控：性能优化、配方调整、工艺改进、参数优化。
- 产业化与转化：生产工艺、规模化、成本、临床转化、商业化、审评审批。
- 评价与标准：安全性评价、质量控制、标准、测试方法。

要求：
1. 生成 4-6 条英文检索式（如果主题较窄，至少3条；如果主题宽泛，可适当增加，但不超过6条）。
2. 每条检索式必须明确对应上述一个或两个紧密相关的子领域，用核心主题词与子领域术语组合，例如：
   - 主题词 AND 子领域关键词
   - 主题词 AND (子领域关键词1 OR 关键词2)
3. 整体上，这些检索式应覆盖核心关键词、方法、应用，并至少触及其他两个子领域（如性能、机理、产业化等），确保检索的全面性。
4. 每条检索式应为完整的英文查询字符串，可使用布尔运算符（AND、OR、NOT）、双引号短语、通配符（*）等常规检索语法。
5. 避免不同检索式之间关键词大量重复，每个子领域的检索应相对独立。
6. 只输出 JSON 数组字符串，数组元素为字符串，例如：["bone repair biomaterials AND bioactivity", "osteogenic scaffolds AND mechanical properties", "biodegradable bone graft AND clinical translation", "bone regeneration AND osteoinductive mechanism"]。
7. 不要输出任何解释、注释或额外文本，确保输出可直接被 JSON 解析。

主题：{topic}"""


CLEAN_PROMPT_TEMPLATE = """你是文献元数据规整器。请根据下面的原始元数据 JSON，输出规范化后的 JSON 对象。

输出格式必须严格符合以下结构：
{
  "title": "标准标题",
  "venue": "期刊/会议/预印本库名",
  "venue_issn": "ISSN 或 null",
  "source_type": "journal|repository|proceedings",
  "publication_status": "Published|Preprint|In Press",
  "pub_year": 2025,
  "pub_date": "YYYY-MM-DD 或 null",
  "doi": "DOI 或 null",
  "authors": [{"name": "...", "orcid": null, "affiliations": ["机构全称"]}]
}

处理规则：
1. 标题和期刊名标准化大小写：标题采用每个主要单词首字母大写（Title Case），保留专有名词和缩写原样；期刊名保持官方大小写（如已知），否则使用 Title Case。
2. 作者信息解析：
   - 若原始元数据中作者缺失或为空，则输出 [{"name": "Unknown", "orcid": null, "affiliations": []}]。
   - 若作者姓名格式为 "Last, First" 或 "First Last"，统一转换为 "First Last"（姓名顺序）。
   - 作者 affiliations 应提取为字符串数组，每个元素为机构全称；若有多位作者，保持原始顺序。
   - orcid 仅在明确提供时填写，否则为 null。
3. 对于 venue_issn 和 doi：只使用原始元数据中明确给出的值；若无法确定，必须输出 null，不得编造。
4. source_type 判断优先级：若元数据包含期刊信息且 DOI 以 10.xxxx 开头且发布在期刊平台，则为 "journal"；若来自 arXiv/bioRxiv/medRxiv 等预印本库，则为 "repository"；若来自会议论文集（如 ACM/IEEE 会议），则为 "proceedings"；无法判断时根据元数据中的 container 字段推断，仍然不确定则输出 "journal"（或 null？建议选择最可能的并可在后续人工复核）。
5. publication_status 判断：若元数据中有明确的出版状态标签（如 "Published"、"Preprint"、"In Press"），直接采用；否则根据发布日期和 DOI 是否存在推断：有 DOI 且有正式卷期页码 → "Published"；来自预印本库且无 DOI → "Preprint"；有接收日期无出版日期 → "In Press"。
6. pub_year 必须为四位整数，若无法确定年份则输出 null（而非 0）。
7. pub_date 格式为 "YYYY-MM-DD"，若只有年份或月份，可补全为当年1月1日或当月1日，并在后续人工校验；完全缺失则 null。
8. 只输出 JSON 对象，不要包含任何额外文字、注释或代码块标记。

原始元数据：{payload}"""


class RetrievalLLM:
    """包装检索节点绑定的 LLM（默认 DeepSeek V4）。"""

    def __init__(self, model, max_queries: int = 6) -> None:
        self.model = model
        self.max_queries = max_queries

    # ---- 1) 查询规划 ----
    def plan_queries(self, topic: str) -> list[str]:
        """基于研究主题生成一组覆盖多子领域的英文检索式（4-6 条）。"""
        prompt = PLAN_PROMPT_TEMPLATE.replace("{topic}", topic)
        try:
            msg = self.model.invoke([HumanMessage(content=prompt)])
            raw = getattr(msg, "content", str(msg))
            parsed = _parse_first_json(raw)
            if isinstance(parsed, list):
                queries = [str(q).strip() for q in parsed if str(q).strip()]
            else:
                queries = [ln.strip() for ln in str(raw).splitlines()
                           if ln.strip() and not ln.startswith(("```", "["))]
            out: list[str] = []
            for q in queries:
                if q not in out:
                    out.append(q)
            if not out:
                out = [topic]
            return out[:self.max_queries]
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 查询规划失败，回退原主题: %s", exc)
            return [topic]

    # ---- 2) 元数据规整/补全 ----
    def clean_metadata(self, rec: dict[str, Any]) -> dict[str, Any] | None:
        """输入多源原始元数据，返回规范化记录（缺失字段尽力补全）。"""
        payload = {
            "title": rec.get("title"), "doi": rec.get("doi"),
            "venue": rec.get("venue"), "venue_issn": rec.get("venue_issn"),
            "source_type": rec.get("source_type"),
            "publication_status": rec.get("publication_status"),
            "pub_year": rec.get("pub_year"), "pub_date": rec.get("pub_date"),
            "authors": rec.get("authors"),
        }
        prompt = CLEAN_PROMPT_TEMPLATE.replace(
            "{payload}", json.dumps(payload, ensure_ascii=False))
        try:
            msg = self.model.invoke([HumanMessage(content=prompt)])
            parsed = _parse_first_json(getattr(msg, "content", str(msg)))
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 元数据规整失败: %s", exc)
        return None
