---
name: journal-quartiles
description: 期刊/会议分区与分区得分表；权威性评分的数据源，支持外部覆盖与扩展
---

# Journal Quartiles（期刊分区技能）

## When to use
- 质量评估节点的权威性因子 A 需要判定期刊分区时；
- 需要为某个学科补充/修正分区表时（不要改代码，改数据）。

## Input / Output
- 输入：规范化后的期刊名（小写、去标点，见 `quality/scoring.py::normalize_venue_name`）；
- 输出：`Q1..Q4` 或缺失（缺失时不猜测，按无分区处理）。

## Data
- `content/data.json`
  - `quartiles`：期刊名 → 分区；
  - `quartile_scores`：分区 → 权威性分值（默认 Q1 0.95 / Q2 0.80 / Q3 0.65 / Q4 0.50）。

## Override
- 环境变量 `RA_JOURNAL_QUARTILES` 指向一个 JSON 文件（同结构），其条目**覆盖**内置条目；
- 这样可以在不改代码、不重打包的前提下补充本校/本领域的期刊分级。

## Rules
- 表里没有的期刊**不加分也不减分**（不要用"未知=低分"的假设）；
- 建议各学科包各自维护一份分区数据，通用表只保留跨学科顶刊。

## 代码入口
- `src/research_agent/packs.py::journal_quartiles` / `quartile_scores`
- `src/research_agent/quality/scoring.py::venue_factor`
