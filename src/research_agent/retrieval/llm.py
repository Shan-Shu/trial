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


class RetrievalLLM:
    """包装检索节点绑定的 LLM（默认 DeepSeek V4）。"""

    def __init__(self, model, max_queries: int = 3) -> None:
        self.model = model
        self.max_queries = max_queries

    # ---- 1) 查询规划 ----
    def plan_queries(self, topic: str) -> list[str]:
        """把主题拆解为若干检索式；失败/无模型时回退 [topic]。"""
        prompt = (
            "你是科研文献检索规划器。用户给出研究主题，请拆解为 2-3 条彼此互补的"
            "英文检索式（含关键词/方法/应用三个角度，避免重复）。\n"
            "只输出 JSON 数组字符串，如 [\"retrieval augmented generation\", ...]，"
            "不要输出其它文字。\n主题：" + topic
        )
        try:
            msg = self.model.invoke([HumanMessage(content=prompt)])
            raw = getattr(msg, "content", str(msg))
            parsed = _parse_first_json(raw)
            if isinstance(parsed, list):
                queries = [str(q).strip() for q in parsed if str(q).strip()]
            else:
                queries = [ln.strip() for ln in str(raw).splitlines()
                           if ln.strip() and not ln.startswith(("```", "["))]
            out = [topic]
            for q in queries:
                if q.lower() != topic.lower() and q not in out:
                    out.append(q)
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
        prompt = (
            "你是文献元数据规整器。请基于下面的原始元数据 JSON，输出规范化 JSON：\n"
            "{\n"
            '  "title": "标准标题",\n'
            '  "venue": "期刊/会议/预印本库名",\n'
            '  "venue_issn": "ISSN 或 null",\n'
            '  "source_type": "journal|repository|proceedings",\n'
            '  "publication_status": "Published|Preprint|In Press",\n'
            '  "pub_year": 2025,\n'
            '  "pub_date": "YYYY-MM-DD 或 null",\n'
            '  "doi": "DOI 或 null",\n'
            '  "authors": [{"name": "...", "orcid": null, "affiliations": ["机构全称"]}]\n'
            "}\n"
            "要求：1) 标题/期刊名标准化大小写；2) 作者若缺失则从现有信息推断为 "
            "[{\"name\":\"Unknown\"}]；3) 不要编造 DOI/ISSN，无法确定给 null；"
            "4) 只输出 JSON，不要多余文字。\n原始元数据：" + json.dumps(payload, ensure_ascii=False)
        )
        try:
            msg = self.model.invoke([HumanMessage(content=prompt)])
            parsed = _parse_first_json(getattr(msg, "content", str(msg)))
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM 元数据规整失败: %s", exc)
        return None
