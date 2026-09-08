# research-agent

基于 **LangGraph** 的多模型协作科研辅助 Agent —— 项目环境骨架与最小可运行演示。

用多个大模型（如 OpenAI / DeepSeek / 通义千问 / Claude / Gemini）分工协作：
研究员A、研究员B 并行产出不同视角草稿，主持人模型综合成稿。
后续可在该骨架上挂接科研工具（arXiv / Tavily / PubMed / PDF 解析 / RAG 记忆）。

---

## 1. 环境总览

| 组件 | 版本 / 说明 |
|---|---|
| Python | 3.12.14（由 uv 管理，位于 `%APPDATA%\uv\python\`） |
| uv | 0.12.10（`%USERPROFILE%\.local\bin\uv.exe`，已加入用户 PATH） |
| 虚拟环境 | `.venv`（项目内，`uv` 自动创建） |
| LangGraph | 1.2.11 |
| LangChain | 1.4.0（core 1.6.2） |
| 多模型适配 | langchain-openai 1.6.0 / langchain-anthropic 1.7.1 / langchain-google-genai 4.4.0 / langchain-community 0.4.2 |
| 科研工具 | arxiv、tavily-python、wikipedia、pymupdf、pypdf、requests |

> 说明：本项目通过 **OpenAI 兼容接口**（`langchain-openai` 的 `ChatOpenAI`）即可接入
> DeepSeek、通义千问、Kimi、GLM 等国内模型，无需额外 SDK。

## 2. 镜像 / 下载源配置（已生效）

所有网络下载均走镜像源，配置位置如下：

- **PyPI 包镜像（清华 TUNA）**
  - 用户级 uv：`%APPDATA%\uv\uv.toml`（`[[index]]` 默认源指向 TUNA）
  - 普通 pip：`%APPDATA%\pip\pip.ini`
- **Python 运行时 / uv 本体**（GitHub Release 加速镜像）
  - `uv.toml` 中 `python-install-mirror = "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"`

如需临时切换官方源，可显式指定环境变量，例如：

```powershell
$env:UV_DEFAULT_INDEX = "https://pypi.org/simple"
```

## 3. 目录结构

```
D:\Desktop\trial
├── .env.example          # API Key / 角色配置模板（复制为 .env 后填写）
├── pyproject.toml        # 项目与依赖声明（uv 管理）
├── data\                 # 本地库：research_agent.db（论文/PDF BLOB/动态本体/日志）
├── src\research_agent\
│   ├── __init__.py       # CLI 入口 main()（多模型演示）
│   ├── models.py         # 多模型工厂：按 provider 构建 ChatModel
│   ├── demo.py           # LangGraph 多模型演示图（A/B 并行 -> 主持人综合）
│   ├── config.py         # 全局配置（评分权重/阈值/路径，可用环境变量覆盖）
│   ├── db.py             # SQLite：论文元数据 / PDF BLOB / 清洗文本 / 质量结果
│   ├── pipeline.py       # 三节点 LangGraph 流水线 + CLI（entry: research-agent-pipeline）
│   ├── retrieval\        # 文献检索节点
│   │   ├── api_clients.py    # 多源聚合（PubMed/arXiv/OpenAlex/Crossref）+ PDF 下载
│   │   ├── pubmed.py         # PubMed 源：NCBI E-utilities 检索 + Europe PMC 全文
│   │   ├── pdf_cleaner.py    # 行级页眉/页脚/页码剔除 → 精校重排文本
│   │   ├── node.py           # 检索节点（search / enrich 回补 / load 三模式）
│   │   └── monitor.py        # 实时监控数据库新文献的轮询接口
│   ├── quality\          # 质量评估节点
│   │   ├── scoring.py        # A/T/Q 公式 + 元数据完整性判定
│   │   └── node.py           # 质量节点 + 人工审核节点
│   ├── knowledge\        # 知识提取节点
│   │   ├── preprocess.py     # 分句 / 分段 / 切块
│   │   ├── extractor.py      # LLM 结构化抽取 + 置信度融合
│   │   ├── node.py           # 知识节点（预处理 → 抽取 → 动态本体写入）
│   │   └── fake.py           # 离线静态 JSON 假模型（--model smoke）
│   ├── dashboard\        # 图形化看板（FastAPI + vis-network 本体图谱）
│   │   ├── app.py            # Web 服务（entry: research-agent-dashboard）
│   │   ├── api.py            # REST 数据接口（本体/文献/质量/日志）
│   │   └── static\           # 前端：本体图谱 / 智能体工作台 / 文献详情
│   └── ontology\
│       └── store.py          # 动态本体图存储（类型注册/节点边合并/溯源/版本）
├── tests\
│   ├── test_pipeline_offline.py  # 流水线离线测试
│   └── test_dashboard_api.py     # 看板 REST API 测试
└── examples\
    └── research_tools.py # 科研工具示例：arXiv 检索 / PDF 全文解析
```

## 4. 快速开始

### 4.1 激活环境

```powershell
# 方式一：uv 直接运行（自动使用 .venv）
uv run python --version

# 方式二：手动激活
.\.venv\Scripts\Activate.ps1
python --version
```

> Windows 控制台中文乱码提示：若终端输出中文为乱码，先执行一次
> `chcp 65001` 或 `$env:PYTHONIOENCODING = "utf-8"` 再运行脚本。

### 4.2 离线演示（无需任何 API Key）

验证 LangGraph 编排 + 并行数据流是否正常：

```powershell
uv run research-agent
# 或
uv run python -m research_agent.demo
```

### 4.3 接入真实多模型

1. 复制 `.env.example` 为 `.env` 并填入至少一组 Key：

```powershell
Copy-Item .env.example .env
# 编辑 .env：填 OPENAI_API_KEY / DEEPSEEK_API_KEY / DASHSCOPE_API_KEY / ANTHROPIC_API_KEY / GOOGLE_API_KEY
```

2. 在 `.env` 中开启真实模式并指定各角色提供商：

```ini
RESEARCH_AGENT_REAL=1
WORKER_A_PROVIDER=openai     # 研究员A：GPT
WORKER_B_PROVIDER=deepseek   # 研究员B：DeepSeek
CHAIR_PROVIDER=qwen          # 主持人：通义千问
```

3. 运行：

```powershell
uv run research-agent
```

各角色也可复用同一提供商；提供商列表见 `SUPPORTED_PROVIDERS`。

## 5. 常用命令备忘

```powershell
uv add <package>                     # 安装依赖（走 TUNA 镜像）
uv add langchain-google-genai        # 示例：新增 Gemini 适配
uv remove <package>                  # 移除依赖
uv sync                              # 按 pyproject.toml 同步环境
uv python install 3.13               # 额外安装其他 Python 版本
uv pip list                          # 查看已装包
```

## 6. 科研工具示例

```powershell
uv run python examples\research_tools.py "graph neural network"
```

- `arxiv_search(query)`：arXiv 检索，无需 Key
- `extract_pdf_text(source)`：本地/URL PDF 全文解析（PyMuPDF）
- 联网搜索：申请 [Tavily](https://tavily.com) Key 填入 `.env` 后，可用
  `langchain_community.tools.tavily_search.tool.TavilySearchResults`

## 7. 下一步建议（按需扩展）

- **工具节点**：用 `@tool` 包装 arxiv/tavily/PDF 解析，经 `ToolNode` 挂到 researcher 节点
- **记忆 / 持久化**：`langgraph-checkpoint`（SQLite/Postgres）+ LangGraph 的 `MemorySaver`
- **动态并行**：用 `Send` API 按问题数动态分发 worker，而非固定两个角色
- **主管路由**：加入 supervisor 节点，用 LLM 决定下一步调用哪个模型/工具
- **RAG**：`chromadb` / `faiss` 对论文建索引，检索增强回答
- **评测**：接 LangSmith 做链路追踪与评测


---

## 8. 三节点科研文献流水线（基于动态本体）

`research-agent-pipeline`：**文献检索 → 质量评估 → 知识提取** 的 LangGraph 状态机，
产出物写入本地 SQLite（`data\research_agent.db`），供动态本体检索/推理使用。

```
START ──> retrieval ──> quality ──┬─(knowledge / flagged)──> knowledge ──> END
        (search|load|enrich)      ├─(enrich)────────────────> retrieval  ① 元数据回补
                                  └─(human)─────────────────> human_review ──> END
```

### 8.1 文献检索节点（retrieval）
- 检索 arXiv（可扩展 Crossref/OpenAlex），下载 PDF 以 **BLOB 存入本地库**（同时保留文件级 sha256）；
- 元数据补全：全部作者、作者单位（OpenAlex/Crossref）、发表情况（期刊/分区/年份）、DOI；
- PDF 行级清洗：剔除页眉/页脚/页码/© 等，产出**精校重排文本**（clean_text + paragraphs）；
- 预留**实时监控接口**：`retrieval/monitor.py` 的 `PaperMonitor` 按水位线轮询新文献，
  可对接定时任务/消息回调（`python -m research_agent.pipeline --monitor`）。

### 8.2 质量评估节点（quality）
- 权威性 `A = 0.5*期刊/出版社分级(JCR/SCI分区) + 0.3*作者H指数 + 0.2*被引次数`；
- 时效性 `T = 0.7*exp(-ln2*age/学科半衰期) + 0.3*(1-age/25)`（学科半衰期：fast 2 / medium 4 / slow 8 年）；
- `Q = 0.6*A + 0.4*T`（A、T、Q ∈ [0,1]）；
- 路由：Q ≥ 0.8 直接送知识提取；0.5 ≤ Q < 0.8 标记后送知识提取；Q < 0.5 人工审核；
- 元数据缺漏 → 发回检索节点回补（≤ 3 轮），仍无法补全 → 人工审核。

### 8.3 知识提取节点（knowledge）
- 文本预处理：分段 → 分句 → 按窗口切块；
- LLM 抽取 **实体/关系/属性/事件**，每条自带模型自评置信度；
- 最终置信度 = `0.6*模型自评 + 0.4*质量权重`（质量权重 = Q，标记文献按 0.85 折扣），
  实现“根据质量评估结果和文本逻辑给出置信度”；
- 写入**动态本体**：节点/边按规范化名去重合并（置信度取 max、别名/属性/来源证据增补），
  未注册的新实体/关系类型自动注册（`Experiment`、`involves` 等），schema 版本号递增。

### 8.4 使用

```powershell
# 真实模式（联网检索 + 下载 PDF + 质量评估 + LLM 知识提取）
uv run research-agent-pipeline --query "retrieval augmented generation" --max-results 5 --model openai

# 无 Key 冒烟（知识提取用静态 JSON 假模型，验证整条链路）
uv run research-agent-pipeline --query "graph neural network" --max-results 2 --model smoke

# 实时监控模式：轮询 data\research_agent.db 的新文献并自动处理
uv run research-agent-pipeline --monitor --poll-interval 60 --model openai
```

配置项（可选环境变量）：`RA_Q_WEIGHT_A/T`、`RA_THRESHOLD_DIRECT/FLAG`、`RA_MAX_META_ATTEMPTS`、
`RA_DB_PATH` 等，见 `config.py`。

### 8.5 本地库表
`papers`（元数据+PDF BLOB+精校文本）、`quality_results`、`ontology_type_registry`
（动态类型）、`ontology_nodes / ontology_edges`（去重合并+溯源）、`ontology_runs`
（版本/统计）、`processing_log`。

### 8.6 离线测试
```powershell
$env:PYTHONPATH = "$PWD\src"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
# 24 个用例：PDF 清洗 / A-T-Q 路由 / 元数据回补循环 / 动态本体合并 /
#           三节点端到端 / 三节点 LLM 驱动 / PubMed 接入 / 看板 REST API
```

## 9. 图形化看板（动态本体 + 智能体工作状态）

本地 Web 看板，直连 `data\research_agent.db` 实时查看：

- **动态本体图谱**：节点/关系可视化（颜色按类型、大小按置信度），支持按类型勾选、
  最低置信度过滤、名称搜索；点击节点查看 属性/别名/来源证据/相邻关系；
- **智能体工作状态**：文献检索 / 质量评估 / 知识提取 / 人工审核 四个工作台卡片，
  展示事件数、处理的文献数与最近事件；
- **输入输出**：文献列表（含 Q 值/决策/提取计数）→ 点击查看详情：
  元数据与作者单位（输入）、A/T/Q 构成、精校正文、处理日志（输出）；
- 顶部统计条 + 最近活动流，**自动刷新（6s）**，随流水线运行实时更新。

```powershell
# 启动看板（默认 http://127.0.0.1:8000）
uv run research-agent-dashboard

# 自定义端口 / 数据库
uv run research-agent-dashboard --port 9000 --db data\research_agent.db
```

打开浏览器访问 `http://127.0.0.1:8000` 即可。
前端依赖（vis-network）已本地化到 `dashboard\static\vendor\`，无需外网 CDN。

REST API（同一服务提供）：`/api/overview`、`/api/papers`、`/api/papers/{key}`、
`/api/agents`、`/api/logs`、`/api/ontology`、`/api/ontology/nodes/{id}`、`/api/health`。

---

## 9. 图谱可视化演示

把动态本体（SQLite 的 ontology_* 表）导出为**单文件交互式图谱**（内嵌本地 vis-network，
离线双击即用）及静态 SVG/PNG 缩略图。

```powershell
# 内置演示数据集（27 节点 / 24 关系，覆盖 4 篇论文）
uv run research-agent-viz --dataset demo

# 导出当前真实本体库（data\research_agent.db）
uv run research-agent-viz --dataset db

# 生成后用默认浏览器打开
uv run research-agent-viz --dataset demo --open
```

产物默认写入 `output\`：
- `ontology_graph_demo.html`：交互图谱 —— 拖拽/缩放、悬停看「属性/别名/来源证据/置信度」、
  顶部类型图例点击可显示/隐藏、搜索框按名称/别名高亮、可暂停布局/复位视角；
- `ontology_graph_demo.png / .svg`：静态预览（节点按类型着色、大小随置信度）。

`ontology/viz.py` 同时提供 API：`export_graph_from_db(db)`、`build_demo_graph()`、
`render_interactive_html(graph)`、`render_static_svg/png(graph)` 可复用到其它界面/报告。

## 10. 三节点 LLM 化（DeepSeek V4 / GLM 4.7 Flash / DeepSeek V4 Pro*）

> \* 知识提取节点当前用 DeepSeek V4 Pro 临时替代 ChatGPT 5.6（OpenAI 在当前网络不可达），
> 可随时切回：`ROLE_PROVIDER_KNOWLEDGE=openai`、`KNOWLEDGE_MODEL=gpt-5.6`。

三个节点现在分别由指定 LLM 驱动（`models.py` 角色绑定表，全部可用 .env 覆盖）：

| 节点 | 默认模型 | provider / 接口 | 所需 API Key |
|---|---|---|---|
| 文献检索（retriever） | `deepseek-v4-pro`（DeepSeek V4，可换 `deepseek-v4-flash`） | deepseek | `DEEPSEEK_API_KEY` |
| 质量评估（quality） | `glm-4.7-flash`（GLM 4.7 Flash） | glm（智谱 BigModel） | `ZHIPU_API_KEY` |
| 知识提取（knowledge） | `deepseek-v4-pro`（DeepSeek V4 Pro，临时替代 gpt-5.6） | deepseek | `DEEPSEEK_API_KEY` |

### 各节点如何“用 LLM 实现”
- **检索节点**：DeepSeek V4 负责「动脑」——把主题拆解为互补检索式（`plan_queries`）、
  规整多源原始元数据补齐作者/单位/DOI（`clean_metadata`）；arXiv/OpenAlex/Crossref
  仍负责实际的检索与下载（LLM 无法联网抓取）。
- **质量节点**：GLM 4.7 Flash 依据文献元数据给出 期刊分区/venue/h/被引 等子项评分与
  学科速度判断；`A/T/Q` 仍按既定公式 `Q=0.6A+0.4T` 计算与路由，保证规则可复现，
  并把 GLM 的评审意见写入 `rationale`。
- **知识节点**：DeepSeek V4 Pro（临时替代 ChatGPT 5.6，因当前网络无法访问
  `api.openai.com`）负责把精校正文抽取为 实体/关系/属性/事件 的结构化 JSON，
  置信度按 `0.6*模型自评+0.4*质量权重` 融合后写入动态本体。

无 Key / 模型不可用时自动回退到确定性实现（检索直接检索、评分走规则公式、
知识提取跳过），不影响流水线运行。

### 使用
```powershell
# 在 .env 填入 DeepSeek 与智谱两把 Key 后（默认 auto 即按上表绑定）：
uv run research-agent-pipeline --query "graph neural network" --max-results 5

# 显式指定/切换某一节点模型（smoke=离线假 LLM，none=关闭该节点 LLM）
uv run research-agent-pipeline --query "RAG" --retriever-llm deepseek `
    --quality-llm glm --knowledge-llm openai

# 无 Key 全链路演示：三角色全部用离线假 LLM（联网检索仍真实）
uv run research-agent-pipeline --query "knowledge graph construction" --llm-smoke
```

模型标识与平台说明（2026 现状）：DeepSeek V4 家族为 `deepseek-v4-pro/flash`；
GLM 4.7 Flash 的模型 code 为 `glm-4.7-flash`（智谱开放平台免费）。知识提取当前
用 DeepSeek V4 Pro 替代 ChatGPT 5.6（OpenAI API id `gpt-5.6`），待 OpenAI 网络
可用后，设 `ROLE_PROVIDER_KNOWLEDGE=openai` + `KNOWLEDGE_MODEL=gpt-5.6` 即可切回。
如你的平台模型 id 不同，直接在 `.env` 改
`RETRIEVAL_MODEL / QUALITY_MODEL / KNOWLEDGE_MODEL` 即可。

新增离线测试（`tests/test_llm_roles.py`）验证：检索节点按 LLM 规划的多查询入库多篇、
质量节点用假 GLM 子项评分覆盖规则结果（含 `[LLM 评审]` rationale 入库）。

## 11. PubMed 文献源（当前默认，临时替代 arXiv）

PubMed 本身不提供 PDF，接入采用「NCBI + Europe PMC」双通道组合：

1. **检索/元数据**：NCBI E-utilities `esearch + efetch`，解析 标题 / 全部作者 /
   作者单位 / 期刊与 ISSN / 年份 / DOI / PMID / PMCID；
2. **全文**：Europe PMC `fullTextXML` 拉取 OA(PMC) 全文 → 转纯文本；有 PMC 时
   尽力尝试 `europepmc.org/articles/{PMCID}?pdf=render` 的 PDF（常被限流）；
3. **回退**：无 OA 全文则用摘要作为提取文本，并在 `papers.fulltext_source`
   记录 `pdf / xml / abstract`。

```powershell
# 用 PubMed 检索处理（--source 默认已是 pubmed）
uv run research-agent-pipeline --query "bone regeneration AND scaffold" --max-results 20 --source pubmed

# 切回 arXiv 或两者同时
uv run research-agent-pipeline --query "..." --source arxiv
uv run research-agent-pipeline --query "..." --source both
```

备注：NCBI 无 Key 限速 ≤3 req/s，代码内置 `delay=0.35s`；如申请了 NCBI Key，
设环境变量 `NCBI_API_KEY` 可提速。

## 12. 四节点研究任务层（规划 / 知识消费 / 内容形成 / 审核校对）

在既有“检索 -> 质量 -> 知识”建库流水线上增加一层面向研究任务的高层编排。
知识消费节点不直接由 LLM 查库：它从动态本体读取可溯源模式卡/证据卡，
语料不足时向现有数据流水线发出补集请求。

```text
planner ──> knowledge_consumer ──> content_builder ──> reviewer
                │  语料不足              ▲                  │
                └── 调用现有 pipeline ────┴─ revise ─────────┘
```

目录：

```text
src/research_agent/study/
├── planner.py     # 工作规划节点：模糊请求 -> 语料采集任务单
├── consumer.py    # 知识消费节点：动态本体 -> 模式卡/证据卡
├── content.py     # 内容形成节点：模式卡/证据卡 -> 可溯源草稿
├── reviewer.py    # 审核校对节点：引用存在性/支持度门控
├── collection.py  # 与现有 retrieval->quality->knowledge 的补集桥接
└── graph.py       # LangGraph 编排与 CLI
```

使用：

```powershell
# 离线确定性运行（不调用 LLM，验证四节点编排）
uv run research-agent-study --request "骨修复支架前沿" --db data\ontology_v05.db --llm-smoke

# 真实模型运行：规划 DeepSeek V4 Flash / 内容 DeepSeek V4 Pro / 审核 GLM 4.7 Flash
uv run research-agent-study --request "RAG 2024-2026 前沿综述" --db data\ontology_v05.db

# 先调用现有检索/质量/知识流水线补充语料，再进入四节点
uv run research-agent-study --request "..." --collect
```

角色可在 `.env` 中通过 `PLANNER_MODEL / CONTENT_MODEL / REVIEW_MODEL` 覆盖。
知识消费节点返回的每一条证据均带 `paper_key`、原文句和 `evidence_id`；
审核节点只允许草稿引用这些真实 ID，禁止内容形成节点自造来源。
