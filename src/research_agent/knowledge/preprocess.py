"""知识提取前的文本预处理：分句、分段、切块。"""
from __future__ import annotations

import re


ABBREVIATIONS = (
    "e.g", "i.e", "etc", "vs", "al", "Fig", "Figs", "Eq", "et", "cf",
    "Dr", "Mr", "Ms", "Prof", "Inc", "Ltd", "Co", "No", "vol", "pp",
    "eds", "Eds", "St", "Mt", "U.S", "U.K", "a.m", "p.m",
)
_ABBR_RE = re.compile(r"(?<!\w)(" + "|".join(ABBREVIATIONS) + r")\.\s*$")


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def split_paragraphs(text: str) -> list[str]:
    """按空行/换行切分为段落，过滤过短片段。"""
    text = normalize_whitespace(text)
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    return paras


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句；跳过常见缩写词后的句点。"""
    text = normalize_whitespace(text)
    parts = re.split(r"(?<=[.!?。！？])\s+", text)
    sentences: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # 缩写如 "et al." / "e.g." 后不该断句：合并回上一句
        if sentences and _ABBR_RE.search(sentences[-1]):
            sentences[-1] = sentences[-1] + " " + p
        else:
            sentences.append(p)
    return sentences


def chunk_paragraphs(paragraphs: list[str], max_chars: int = 8000,
                     max_chunks: int | None = None) -> list[list[str]]:
    """把段落序列切成若干文本窗口（每窗不超过 max_chars）。"""
    chunks: list[list[str]] = []
    cur: list[str] = []
    cur_len = 0
    for para in paragraphs:
        plen = len(para)
        if cur and cur_len + plen > max_chars:
            chunks.append(cur)
            cur = []
            cur_len = 0
        cur.append(para)
        cur_len += plen
    if cur:
        chunks.append(cur)
    if max_chunks:
        chunks = chunks[:max_chunks]
    return chunks
