# HQC / 基于编码的后量子密码：三版综述交付物

> 数据底座：`data/hqc_code_based_crypto.db`（本项目新建的独立数据库）
> 领域 skill：`packs/domains/code_based_crypto/`（节点分布 + 6 类超边形式 + 量化词表 + 语料硬门）
> 生成链：Planner → Consumer → Content → Reviewer →（Fact-check）→ 库内编号转数字引用
> 生成时间：2026-09-19（本地）

## 一、三个版本

| 文件 | 版本 | 形态 | 摘要 | 参考文献 |
|---|---|---|---|---|
| [review_zh.md](review_zh.md) | 中文版 | 中文正文 + 编号上标引用 + 中文「参考文献」 | `## 摘要`（单段） | 22 |
| [review_zh_en.md](review_zh_en.md) | 中英版 | 中文正文 + 独立英文 `## Abstract` + 编号引用 | `## Abstract`（单段） | 21 |
| [review_en.md](review_en.md) | 英文 IEEE 版 | 英文正文 + IEEE 参考文献格式（`[n] A. Author, "Title," *Journal*, vol., no., pp., Year. doi:`） | `## Abstract`（单段） | 18 |

自检（`--polish-only` 后）：三版 **`clean = true`** —— 无残留库内编号、无系统术语、无占位符；
`dangling_citations = 0`、`duplicate_dois = 0`。**段落化**：`bullet_lines = 0`（正文没有
要点罗列，全部为自然段），`paragraph_style = true`；**摘要**：`abstract_present = true`
且为单段连贯文字（此前草稿的 `summary` 字段未被渲染、摘要缺失，已由 `ensure_abstract` 补上）。
逐条引用可经 `summary.json` / `review_*.json` 的 `citation_map`（编号 → paper_key → DOI）
回溯到库内文献。

> 说明：三版的 Reviewer 判定均为 `manual_review`（而非 `pass`），原因是审核节点要求
> "HQC 专属安全归约、HQC 参数集上的 ISD 复杂度、解码失败攻击对 HQC 的具体影响"等证据，
> 而现有语料只有**邻近代码场景**的归约与攻击证据。综述因此把这些点明确写成
> **开放问题**（正文中标注"该问题在现有语料中缺少直接证据"），而不是用邻近证据冒充。
> 这是有意保留的诚实性约束，不是生成失败。

## 二、数据底座规模（生成时可核验）

| 指标 | 数值 |
|---|---|
| 在范围内文献 | **331 篇**（目标 ≥300） |
| 语料硬门剔除的界外文献 | 348 篇（纯编码理论、格密码、机械工程等同名词噪声） |
| 知识提取完成 | **340 篇** |
| 全文来源 | PDF 全文 330 篇 / 摘要 1 篇（arXiv 全文优先，符合约定） |
| 正文字符总量 | 10,443,569 |
| 本体 | 节点 14,335 ／关系边 24,479 ／超边 8,354 ／条件 7,375 ／测量 5,514 |
| 密码学超边 | `construction` 339、`attack` 260、`parameterization` 206、`benchmark` 146、`failure_analysis` 97、`security_reduction` 39 |

## 三、复现命令

```bash
# 1) 抓取（多检索式 + 语料硬门 + 断点续跑）
python examples/run_domain_corpus.py --db data/hqc_code_based_crypto.db \
    --domain code_based_crypto --source arxiv --per-query 40 --skip-known --max-papers 330
# 2) 知识提取（flash 并行）
python examples/run_domain_corpus.py --db data/hqc_code_based_crypto.db \
    --domain code_based_crypto --extract-only --workers 12 --model flash --max-chunks 3
# 3) 三版综述
python examples/run_domain_review.py --db data/hqc_code_based_crypto.db \
    --domain code_based_crypto --out-dir output/hqc_review --variant all --fast-roles
# 4) 收尾清理（裸编号/系统术语）
python examples/run_domain_review.py --db data/hqc_code_based_crypto.db \
    --out-dir output/hqc_review --polish-only
```

查看进度（项目原后端看板）：`python -m research_agent.dashboard.app --db data/hqc_code_based_crypto.db --port 8010`
