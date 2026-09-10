# Versions

research-agent（LangGraph 多模型科研辅助 Agent）本地提交版本索引。

| 版本 | Commit | 说明 |
|---|---|---|
| v0.0.1 | 4935125b5d56c9fdb101c440992d8d05cdbaa74c | 初始快照（旧检索提示词基线） |
| v0.0.2 | 5ca0a137db6a80763b3d11556d3ccbcf5bd2c346 | 检索/质量/知识提示词升级 + 关系同义归一 |
| v0.0.3 | 0399042fd15908b518dc93b9eb919a1800e85465 | 防衔接语实体过滤 + 关系词表细粒度化 + 配方细节保留 |
| v0.0.4 | 69950303510cf3ccf139936c9678bb7fd7cfdf0a | 事件节点名词化 + involves 克制 + 四版本对比产物 |

| v0.0.5 | a8d92f9125a832900e7b8b7884197b3a55d40328 | 事件旁路化(event_assertions)、实体双轨身份+材料登记(lcmat)、证据等级/scope、细粒度关系、强断言置信门控、合并/方向队列 |
| v0.0.6 | cafb20c0a4e2ea877efa44725fdfa9d70aa81695 | 三开源学习报告(docs/open_source_agents_learning_report.md)、知识提取硬性禁区清单(ERROR_LIST)、低置信/泛化关系定向精修(extract_with_refine)、语料兜底关系提醒、skills 同步、精修开关/阈值(RA_KNOWLEDGE_REFINE 等) |
| v0.0.6.1 | 7009c5033faf56c5a771ab6ad8f2b75a1460607a | 提速补丁：并行批量提取脚本(examples/run_knowledge_batch.py，多 worker/WAL/预建连接/逐篇进度)，知识节点 run_init 开关消除并发建表锁；pro 2 篇并行 268s≈串行一半 |

> 注：v0.0.5 标签随后续“属性质量优化(模板字段/去空值/snake_case/value+unit/扫描器)”前移至最新提交，精确指针以 git tag 为准。
> 注：v0.0.6 标签随本表登记提交前移，精确指针以 git tag 为准。
> 注：v0.0.6.1 标签随本表登记提交前移，精确指针以 git tag 为准。

| v0.1.0 | dd9ab247ff9f4909b2993cf988639cd5c51117c6 | 里程碑版本：研究任务四节点 + 金标准 pilot + 提示词清单/审阅文档 + 模型绑定统一为 DeepSeek V4 Flash / GLM 4.7 Flash 审核 |
| v0.1.1 | 2230cd7d56f15fb07d81f4bce318630ded07aa0f | P1 领域画像优化：工作规划生成 DomainProfile、检索不再内置生物医药维度、质量缺失数据不估计、知识抽取支持领域候选/冻结 schema |
| v0.1.2 | 0ad8b9ea3ded37c063c7c731d24dd7d1e5ce1097 | 通用生成型任务支持：planner 识别 generative 任务、consumer 输出 design_context、content 生成候选 strategies、reviewer 增加创新充分性检查 |

| v0.2.0 | c49f11715e788ddf152f732355a6b14c52a35402 | 检索专项 skills（证据缺口/查询扩展/引用溯源）、质量控制与领域词典全局归并、多库看板与规划交互、人工审核界面、NCPSSD 中文社科语料接入 |

| v0.3.0 | 7390b9906a1ecab7b192d3c13d6163c1ad4526e7 | 审核节点 4:1 双维度与正确性硬门槛、科研超边本体、语义节点域/关系通道、局部编号清理、可视化超边面板 |
