"""科研文献 API 客户端：arXiv / OpenAlex / Crossref + PDF 下载 + 元数据合并。

所有外部调用均带超时与容错：单源失败不影响整体流程（返回 None / 空结果）。
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

import requests

from research_agent.config import settings
from research_agent.retrieval.pubmed import PubMedClient

logger = logging.getLogger(__name__)


def _safe_get(url: str, params: dict | None = None, timeout: int | None = None) -> dict | None:
    try:
        resp = requests.get(
            url, params=params, timeout=timeout or settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001 —— 外部服务不可用不应中断流程
        logger.warning("HTTP 请求失败 %s: %s", url, exc)
        return None


def download_pdf(url: str, timeout: int | None = None) -> bytes | None:
    """下载 PDF 二进制内容；失败返回 None。"""
    try:
        resp = requests.get(
            url, timeout=timeout or settings.http_timeout,
            headers={"User-Agent": settings.user_agent},
            stream=True,
        )
        resp.raise_for_status()
        ctype = resp.headers.get("Content-Type", "")
        if "pdf" not in ctype and not resp.url.lower().endswith(".pdf"):
            logger.warning("疑似非 PDF 响应: %s (%s)", resp.url, ctype)
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("PDF 下载失败 %s: %s", url, exc)
        return None


def _canonical_authors(authors: list[dict]) -> list[dict]:
    out = []
    for a in authors or []:
        out.append({
            "name": a.get("name") or " ".join(
                filter(None, [a.get("given"), a.get("family")])
            ) or "Unknown",
            "given": a.get("given"),
            "family": a.get("family"),
            "orcid": a.get("orcid"),
            "h_index": a.get("h_index"),
            "affiliations": list(a.get("affiliations") or []),
        })
    return out


def merge_metadata(base: dict[str, Any], extra: dict[str, Any] | None) -> dict[str, Any]:
    """用 extra 补全 base 中缺失的字段（不覆盖已有值）。作者按姓名位置合并补全。"""
    if not extra:
        return base
    rec = dict(base)
    for key in ("title", "abstract", "doi", "venue", "venue_issn", "source_type",
                "pub_year", "pub_date", "publication_status", "citation_count",
                "avg_h_index", "pdf_url"):
        if (rec.get(key) in (None, "", []) ) and extra.get(key) not in (None, ""):
            rec[key] = extra[key]
    # 作者补全：位置对齐，缺 name 或 affiliations 时用 extra 填充
    base_authors = list(rec.get("authors") or [])
    extra_authors = list(extra.get("authors") or [])
    if not base_authors:
        rec["authors"] = _canonical_authors(extra_authors)
    elif extra_authors:
        merged = []
        for i, a in enumerate(base_authors):
            b = extra_authors[i] if i < len(extra_authors) else {}
            affs = list(a.get("affiliations") or b.get("affiliations") or [])
            if not a.get("name") and b.get("name"):
                a["name"] = b["name"]
            merged.append({
                **a,
                "affiliations": affs,
                "h_index": a.get("h_index") or b.get("h_index"),
                "orcid": a.get("orcid") or b.get("orcid"),
            })
        rec["authors"] = merged
    return rec


class ArxivSearcher:
    """arXiv 检索：命中结果自带 PDF 地址，可直接下载。"""

    def search(self, query: str, max_results: int = 5) -> list[dict]:
        try:
            import arxiv
        except ImportError:
            logger.warning("缺少 arxiv 包")
            return []
        results: list[dict] = []
        try:
            client = arxiv.Client(page_size=max_results, delay_seconds=1, num_retries=1)
            for r in client.results(arxiv.Search(query=query, max_results=max_results)):
                short_id = r.get_short_id()
                pdf_url = getattr(r, "pdf_url", None) or f"https://arxiv.org/pdf/{short_id}"
                results.append({
                    "paper_key": f"arxiv:{short_id}",
                    "source": "arxiv",
                    "title": (r.title or "").strip().replace("\n", " "),
                    "abstract": (r.summary or "").strip().replace("\n", " "),
                    "doi": r.doi,
                    "venue": "arXiv",
                    "venue_issn": None,
                    "source_type": "repository",
                    "pub_year": r.published.year if r.published else None,
                    "pub_date": r.published.date().isoformat() if r.published else None,
                    "publication_status": "Preprint",
                    "citation_count": None,
                    "avg_h_index": None,
                    "authors": [{"name": a.name} for a in (r.authors or [])],
                    "pdf_url": pdf_url,
                })
        except Exception as exc:  # noqa: BLE001
            logger.warning("arXiv 检索失败: %s", exc)
        return results


class OpenAlexClient:
    """OpenAlex：被引、来源（期刊）信息、作者单位与 H 指数。"""

    BASE = "https://api.openalex.org"

    def work_by_doi(self, doi: str) -> dict | None:
        return _safe_get(f"{self.BASE}/works/https://doi.org/{quote(doi.strip(), safe='')}")

    def search_works(self, title: str, per_page: int = 3) -> list[dict]:
        data = _safe_get(f"{self.BASE}/works", params={
            "search": title, "per-page": per_page,
            "select": "id,doi,title,publication_year,publication_date,cited_by_count,"
                      "primary_location,authorships,open_access,concepts",
        })
        return (data or {}).get("results") or []

    def author_h_indices(self, author_ids: list[str], limit: int = 5) -> dict[str, float]:
        """按 OpenAlex author id 批量取 H 指数（最多 limit 位作者）。"""
        if not author_ids:
            return {}
        ids = "|".join(author_ids[:limit])
        data = _safe_get(f"{self.BASE}/authors", params={
            "filter": f"openalex:{ids}",
            "select": "id,summary_stats",
            "per-page": limit,
        })
        out: dict[str, float] = {}
        for r in (data or {}).get("results") or []:
            h = ((r.get("summary_stats") or {}).get("h_index"))
            if h is not None:
                out[r["id"]] = float(h)
        return out

    def enrich(self, rec: dict[str, Any]) -> dict[str, Any]:
        """用 OpenAlex 补全：DOI、来源期刊、被引、作者单位、H 指数等。"""
        work = None
        if rec.get("doi"):
            work = self.work_by_doi(rec["doi"])
        if not work and rec.get("title"):
            for hit in self.search_works(rec["title"]):
                if _title_similar(hit.get("title"), rec.get("title")):
                    work = hit
                    break
        if not work:
            return rec

        loc = work.get("primary_location") or {}
        src = loc.get("source") or {}
        oa = work.get("open_access") or {}
        authors = []
        author_ids: list[str] = []
        for a in (work.get("authorships") or []):
            inst = [(i or {}).get("display_name") for i in (a.get("institutions") or [])]
            inst = [x for x in inst if x]
            raw = (a.get("raw_affiliation_strings") or [])
            auth = (a.get("author") or {})
            if auth.get("id"):
                author_ids.append(auth["id"])
            authors.append({
                "name": auth.get("display_name"),
                "orcid": auth.get("orcid"),
                "affiliations": inst or raw,
                "openalex_id": auth.get("id"),
            })
        h_indices = self.author_h_indices(author_ids)
        for a in authors:
            oid = a.pop("openalex_id", None)
            if oid and oid in h_indices:
                a["h_index"] = h_indices[oid]
        h_vals = [a["h_index"] for a in authors if a.get("h_index") is not None]

        extra = {
            "title": work.get("title"),
            "doi": work.get("doi"),
            "pub_year": work.get("publication_year"),
            "pub_date": work.get("publication_date"),
            "citation_count": work.get("cited_by_count"),
            "venue": src.get("display_name") or rec.get("venue"),
            "venue_issn": src.get("issn_l") or rec.get("venue_issn"),
            "source_type": src.get("type") or rec.get("source_type"),
            "publication_status": "Published" if work.get("publication_date") else rec.get(
                "publication_status"),
            "authors": authors,
            "avg_h_index": (sum(h_vals) / len(h_vals)) if h_vals else None,
            "pdf_url": (oa.get("oa_url") or (loc.get("pdf_url"))) or rec.get("pdf_url"),
        }
        return merge_metadata(rec, extra)


def _title_similar(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    na = re.sub(r"[^a-z0-9]+", " ", a.lower()).strip()
    nb = re.sub(r"[^a-z0-9]+", " ", b.lower()).strip()
    if len(na) < 10 or len(nb) < 10:
        return False
    short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
    return short in long or _overlap_ratio(na, nb) >= 0.7


def _overlap_ratio(a: str, b: str) -> float:
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / min(len(sa), len(sb))


class CrossrefClient:
    """Crossref：DOI 反查与元数据补全（作者、单位、发表情况）。"""

    BASE = "https://api.crossref.org/works"

    def lookup_by_title(self, title: str) -> dict | None:
        data = _safe_get(self.BASE, params={
            "query.title": title, "rows": 3,
            "select": "DOI,title,author,container-title,type,published-print,published-online",
        })
        for item in (data or {}).get("message", {}).get("items") or []:
            t = (item.get("title") or [""])[0]
            if t and _title_similar(t, title):
                return self._normalize(item)
        return None

    def lookup_by_doi(self, doi: str) -> dict | None:
        data = _safe_get(f"{self.BASE}/{quote(doi.strip(), safe='')}")
        if not data:
            return None
        return self._normalize(data.get("message") or {})

    @staticmethod
    def _normalize(msg: dict) -> dict:
        authors = []
        for a in msg.get("author") or []:
            affs = [x.get("name") for x in (a.get("affiliation") or [])]
            authors.append({
                "given": a.get("given"),
                "family": a.get("family"),
                "name": f"{a.get('given', '')} {a.get('family', '')}".strip(),
                "orcid": (a.get("ORCID") or "").replace("http://orcid.org/", ""),
                "affiliations": [x for x in affs if x],
            })
        date = msg.get("published-print") or msg.get("published-online") or {}
        parts = date.get("date-parts") or [[None]]
        year = parts[0][0]
        return {
            "title": (msg.get("title") or [None])[0],
            "doi": msg.get("DOI"),
            "venue": (msg.get("container-title") or [None])[0],
            "source_type": "journal" if msg.get("type") == "journal-article" else msg.get("type"),
            "publication_status": "Published",
            "pub_year": year,
            "pub_date": f"{year}-01-01" if year and parts[0] and len(parts[0]) > 1
                        else (f"{year}" if year else None),
            "authors": authors,
        }


class ApiHub:
    """统一入口：多源检索（PubMed/arXiv）→ 逐条补全。

    source: 'pubmed' | 'arxiv' | 'both'，默认由调用方（pipeline --source）指定。
    """

    def __init__(self, arxiv: ArxivSearcher | None = None,
                 openalex: OpenAlexClient | None = None,
                 crossref: CrossrefClient | None = None,
                 pubmed: PubMedClient | None = None,
                 source: str = "arxiv") -> None:
        self.arxiv = arxiv or ArxivSearcher()
        self.openalex = openalex or OpenAlexClient()
        self.crossref = crossref or CrossrefClient()
        self.pubmed = pubmed or PubMedClient()
        self.source = source

    def search(self, query: str, max_results: int = 5,
               source: str | None = None) -> list[dict]:
        """按 source 检索并补全元数据，返回规范记录列表。跨源按 key/DOI 去重。"""
        source = (source or self.source).lower()
        raw: list[dict] = []
        if source in ("pubmed", "both"):
            raw += self.pubmed.search(query, max_results=max_results)
        if source in ("arxiv", "both"):
            raw += self.arxiv.search(query, max_results=max_results)
        uniq: list[dict] = []
        seen_keys: set[str] = set()
        seen_dois: set[str] = set()
        for hit in raw:
            key = hit.get("paper_key")
            doi = (hit.get("doi") or "").strip().lower()
            if key in seen_keys or (doi and doi in seen_dois):
                continue
            seen_keys.add(key)
            if doi:
                seen_dois.add(doi)
            uniq.append(hit)
            if len(uniq) >= max_results:
                break
        enriched: list[dict] = []
        for hit in uniq:
            try:
                enriched.append(self.enrich(hit))
            except Exception as exc:  # noqa: BLE001
                logger.warning("补全失败 %s: %s", hit.get("paper_key"), exc)
                enriched.append(hit)
        return enriched

    def enrich(self, rec: dict[str, Any]) -> dict[str, Any]:
        """尽力补全：OpenAlex 优先，Crossref 兜底。"""
        rec = self.openalex.enrich(rec)
        if not (rec.get("doi") and rec.get("venue") and rec.get("authors")):
            extra = None
            if rec.get("doi"):
                extra = self.crossref.lookup_by_doi(rec["doi"])
            elif rec.get("title"):
                extra = self.crossref.lookup_by_title(rec["title"])
            if extra:
                rec = merge_metadata(rec, extra)
        return rec

    def download_pdf(self, rec: dict[str, Any]) -> bytes | None:
        """按候选地址依次尝试下载 PDF。"""
        candidates = []
        if rec.get("pdf_url"):
            candidates.append(rec["pdf_url"])
        if rec.get("source") == "pubmed" and rec.get("pmcid"):
            candidates.append(
                f"https://europepmc.org/articles/{rec['pmcid']}?pdf=render")
        key = rec.get("paper_key") or ""
        if key.startswith("arxiv:"):
            aid = key.split(":", 1)[1]
            candidates.append(f"https://export.arxiv.org/pdf/{aid}")
            candidates.append(f"https://arxiv.org/pdf/{aid}")
        for url in dict.fromkeys(candidates):
            data = download_pdf(url)
            if data:
                return data
        return None

    def fulltext_text(self, pmcid: str) -> str | None:
        """Europe PMC OA 全文文本（PubMed 源无 PDF 时的回退）。"""
        return self.pubmed.fetch_fulltext_text(pmcid)
