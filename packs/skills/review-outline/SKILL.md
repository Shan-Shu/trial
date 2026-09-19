# review-outline —— 综述/前沿类写作结构

## 何时用

内容形成节点（`study/content.py`）在 `task_kind != "generative"` 时读取本技能包，得到：

- `sections` / `sections_en`：必须使用的小节标题（中文/英文）；
- `language`：默认写作语言；
- `abstract_hint` / `abstract_hint_en`：摘要写法要求；
- `terminology_note` / `terminology_note_en`：术语保留策略；
- `section_min_chars`：每节最少字数；
- `review_target_chars`：默认目标篇幅（可被 Planner 的 `budget.review_target_chars` 覆盖）。

## 契约

- 领域包在 `domain.json` 声明 `review_outline`，字段名与本文件一致；
- 列表字段（`sections` / `sections_en`）**领域声明即覆盖**——综述结构是学科性的
  （化学关心"成环策略/催化体系"，密码学关心"参数与安全等级/攻击与复杂度/实现与侧信道"）；
- 其它标量字段按 key 覆盖，缺省用本文件的值；
- 语言选择：调用链传入 `language`（如英文综述传 `en`），否则用 `language` 字段。

## 代码入口

- `research_agent/packs.py::review_outline(domain_kind, language)`
- `research_agent/study/content.py`（REVIEW_BLOCK 由该结构渲染）
