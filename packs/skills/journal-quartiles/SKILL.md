---
name: journal-quartiles
description: 期刊/会议分区与分区得分表；权威性评分的数据源，支持 JCR 导入、分科子集与外部覆盖
---

# Journal Quartiles（期刊分区技能）

## When to use
- 质量评估节点的权威性因子 A 需要判定期刊分区时；
- 需要为某个学科补充/修正分区表时（不要改代码，改数据）。

## Input / Output
- 输入：期刊名（任意大小写与标点，内部按 `packs.normalize_journal_key` 归一：
  大小写/连字符/标点/副标题括号/`the`/`and` 统一）；
- 输出：`Q1..Q4` 或 `None`（**缺失时不猜测**，由 `venue_factor` 按来源类型给中性值）。

## Data
- `content/data.json`
  - `quartiles`：跨学科顶刊种子（人工维护）；
  - `quartile_scores`：分区 → 权威性分值（默认 Q1 0.95 / Q2 0.80 / Q3 0.65 / Q4 0.50）；
- `content/quartiles_<tag>.json`：**分科子集**，由 JCR 名单生成（见下）。

## 从 JCR 名单生成（推荐做法）
```powershell
# 子集进仓库（默认 tag=chemistry，按学科类别过滤），全量表落到 data/（不入库）
uv run python examples/import_jcr_xlsx.py \
    --xlsx "2026年度JCR期刊名单（完整版）.xlsx" --tag chemistry
```
产出：
- `packs/skills/journal-quartiles/content/quartiles_chemistry.json`（默认生效，489 条）
- `data/jcr/jcr_quartiles_full.json`（20337 条；用 `RA_JOURNAL_QUARTILES` 启用）

两者的格式都是**平铺字典** `{"journal name": "Q1"}`，
也兼容 `{"quartiles": {...}}` 包装与 `{"data": ...}` 之外的其它键（非字符串值忽略）。

## Override
- `RA_JOURNAL_QUARTILES` 可指向一个或多个 JSON 文件（`;` 分隔），后加载者覆盖前者；
- 典型用法：`.env` 里指向全量 JCR 表，本地生效但不进仓库。

## Rules
- 表里没有的期刊**不加分也不减分**；
- 查询一律走 `packs.journal_quartile(name)`，不要自己拼 key；
- **数据来源声明**：`quartiles_chemistry.json` 与全量表由使用者提供的 JCR 名单导入，
  JCR 为 Clarivate 授权数据，仓库内只保留本领域子集，全量表放在被忽略的 `data/`。

## 代码入口
- `src/research_agent/packs.py::journal_quartiles` / `journal_quartile` /
  `normalize_journal_key` / `quartile_scores`
- `src/research_agent/quality/scoring.py::venue_factor`
- `examples/import_jcr_xlsx.py`
