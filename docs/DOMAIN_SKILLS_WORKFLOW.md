# 新增一个研究领域：pack 工作流

> 原则：**每碰到一个新的研究领域，就把它做成一个 pack（skill），不改 Python。**
> 本文以 `code_based_crypto`（HQC 为核心的基于编码密码学）为例，给出可复制的步骤。

## 0. 为什么

代码里一旦内联学科内容，换领域就必须改代码：化学的"温度/溶剂/成环策略"会污染密码学
的抽取提示词与综述结构（本仓库 v0.4.5 之前确实如此——`content.py` 的综述小节写死了
"成环与环加成策略"，`extractor.py` 的条件键写死了"temperature/solvent"）。
pack 化的收益：换领域 = 加目录 + 跑脚本，且**缺包时显式告警**而不是静默用错词表。

## 1. 需要写的文件

```text
packs/domains/<kind>/
├── domain.json          # 领域画像 + 关键词 + 节点类型 + 超边形式 + 量化词表 + 写作结构 + 语料硬门
├── SKILL.md             # 设计说明：节点分布、超边形式、语义约定（给人/Agent 读）
├── corpus_queries.json  # 该领域的检索式（按来源分组）
└── vocab/
    ├── terms.jsonl      # 外部身份/规范名与别名（term/id/canonical/aliases/source）
    └── mechanism.jsonl  # 机制词表增补（必要时用 "mode": "replace" 整体替换他学科词表）
```

## 2. `domain.json` 的字段与合并语义

| 字段 | 作用 | 合并语义 |
|---|---|---|
| `kind` / `keywords` / `alias_keywords` | 领域判定（`packs.infer_domain_kind`） | 命中最多关键词者胜，都不命中用 `fallback: true` 的领域 |
| `profile.dimensions` | 评价维度（Planner 用） | 领域声明即用 |
| `profile.candidate_entity_types` / `candidate_relation_types` | 注入抽取提示词的候选类型 | 注入提示词 |
| `profile.schema_status` | `candidate` / `frozen` | frozen 时提示词要求"不得新增类型" |
| `extra_seed_node_types` / `extra_seed_relation_types` | 本体初始化时的种子类型 | 与核心词表**并集** |
| `extraction_schema.hyperedge_types` | 超边形式（type/label/member_roles/condition_keys/measurement_metrics） | 与基础层**追加**（按 type 去重） |
| `extraction_schema.condition_keys` / `measurement_metrics` | 量化词表（键、标签、单位） | 领域声明**整体覆盖**基础层（避免跨学科量纲混用） |
| `extraction_schema.quantitative_rules` | 哪些超边必须填 conditions/measurements | 领域声明即覆盖 |
| `extraction_schema.hyperedge_quota` | 消费节点按类型的检索预算 | 按 key 合并（领域同名键覆盖） |
| `review_outline.sections` / `sections_en` | 综述小节 | 领域声明即覆盖 |
| `review_outline.terminology_note` / `section_min_chars` / `review_target_chars` | 术语策略/篇幅 | 按 key 覆盖 |
| `corpus_filter.groups_all` | 语料硬门：每个分组至少命中一个词 | 全部命中才算在范围内；不声明则不限制 |

## 3. 设计顺序（建议）

1. **先划节点分布**：枢纽节点（被多数关系经过的）与叶子节点分开，避免把度量建成孤立节点。
   → 例：密码学的枢纽是 `Scheme`/`CodeFamily`，度量（`work_factor`/`dfr`）只挂在超边 measurements 上。
2. **再定超边形式**：本领域里"一次可核查的机制性断言"是什么？
   → 化学是"反应（底物→产物，条件+产率）"；密码学是"攻击/归约/构造/参数化/失败分析/基准"。
   每类给出 `member_roles` + `condition_keys` + `measurement_metrics`。
3. **然后定量化词表**：条件键与度量指标要带单位（`dfr` 用 `log2`、尺寸用 `bytes`、周期用 `cycles`），
   并在 `quantitative_rules` 里写清"哪类超边必须给哪些键"。
4. **机制/算子词表**：本领域的"机制"是什么？密码学 = 归约与解码/攻击路径；
   "算子"= keygen/encaps/decaps/syndrome/ISD/FO/常数时间加固等。若基础词表是他学科的，
   用 `{"section": "...", "mode": "replace"}` 整体替换。
5. **语料硬门**：用两组词（"领域结构词" + "领域问题词"）做交集门，
   防止 `mechanism`、`codes` 这类通用词把机械工程/格密码论文带进库。
6. **写作结构**：综述小节按本领域读者预期写（密码学要"参数与安全等级/攻击与复杂度/实现与侧信道"）。

## 4. 跑起来

```bash
# 1) 抓取（arXiv 全文优先；语料硬门逐检索式过滤；断点续跑）
python examples/run_domain_corpus.py --db data/<name>.db --domain <kind> \
    --source arxiv --per-query 12 --max-papers 320

# 2) 知识提取（并行；flash 提速）
python examples/run_domain_corpus.py --db data/<name>.db --domain <kind> \
    --extract-only --workers 12 --model flash --max-chunks 3

# 3) 抽查（节点/超边/条件/度量是否符合领域 schema）
python examples/inspect_domain_corpus.py --db data/<name>.db --sample 5 --titles 20

# 4) 三版综述（中文+编号引用 / 中文+英文摘要 / 英文 IEEE）
python examples/run_domain_review.py --db data/<name>.db --domain <kind> \
    --out-dir output/<name>_review --variant all --fast-roles

# 5) 看板（项目原后端）
python -m research_agent.dashboard.app --db data/<name>.db --port 8010
```

## 5. 验收清单

- [ ] `python -m unittest tests.test_extraction_schema_packs tests.test_review_outline_packs` 通过；
- [ ] 抽查输出中 conditions/measurements 的键**全部属于本领域**（无他学科量纲）；
- [ ] 超边类型分布覆盖本领域声明的各类形式；
- [ ] 在范围内论文数 ≥ 目标，且 `out_of_scope` 的剔除理由可解释（可打印被剔论文标题复核）；
- [ ] 综述的库内编号全部解析为数字引用，`checks.clean = true`（无残留编号/悬空引用）。
