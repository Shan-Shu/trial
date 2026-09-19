# extraction-schema —— 抽取 schema 的跨领域默认层

## 何时用

知识抽取节点（`knowledge/extractor.py`）在构造提示词时读取本技能包，得到：

- 实体类型建议列表（`entity_types`）；
- 事件类型列表（`event_types`）；
- 超边类型及其成员角色（`hyperedge_types`）；
- 超边通用成员角色（`member_roles`）；
- **可量化字段的词表**：conditions 的键（`condition_keys`）与 measurements 的指标
  （`measurement_metrics`）、示例（`quantitative_examples`）；
- conditions/measurements 的**强制性规则**（`quantitative_rules`）：哪些超边类型
  必须填 conditions、哪些必须填 measurements，以及该领域的键清单；
- 消费节点的超边检索配额（`hyperedge_quota`）。

## 契约

| 字段 | 合并语义 |
|---|---|
| `entity_types`、`hyperedge_types`、`member_roles` | **追加**：基础层 ∪ 领域包 |
| `condition_keys`、`measurement_metrics`、`quantitative_examples`、`quantitative_rules` | **领域包声明即覆盖**（避免把别的学科的量纲塞进本领域提示词） |
| `hyperedge_quota` | **按 key 合并**：领域同名键覆盖基础层 |
| `event_types` | 追加（去重） |

领域包在 `domain.json` 里声明 `extraction_schema`，字段名与本文件一致。

## 规则（重要）

1. **本文件不得出现单一学科的结论性内容**：它只承载"抽取契约"层面的默认值。
   具体学科的量纲、指标、超边形态一律写进 `packs/domains/<kind>/extraction_schema`。
2. **缺包不静默**：读不到本技能包或领域包时，加载器 `packs.warn_once` 告警并退化到
   内置最小默认，不抛异常、也不假装成功。
3. **新增学科 = 新增领域包**，不改 Python。步骤见 `packs/README.md`。

## 代码入口

- `research_agent/packs.py::extraction_schema(domain_kind)`
- `research_agent/packs.py::hyperedge_quota(domain_kind)`
- `research_agent/knowledge/extractor.py::build_prompt`
- `research_agent/study/consumer.py`（超边检索配额）
