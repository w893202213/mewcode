# 记忆渐进式披露（方案 A：召回工具）Plan

## 架构概览

新增一个只读工具 `recall_memory`，在 CLI 装配期构造（注入默认 provider 与两级记忆目录），运行时模型主动调用：

```
模型调用 recall_memory(query)
  → 读两级 index.md 摘要行（两份皆空 → 直接返回「没有找到相关记忆」）
  → 同步调 LLM：固定系统提示 + query + 摘要行 → 输出选中条目（级别+slug，≤3）
  → 按条目读 <slug>.md 全文，标注来源后拼接返回
  → 解析失败 / 判为无相关 / 文件全缺失 → 「没有找到相关记忆」成功结果
```

索引瘦身：`NoteManager` 写回上限收紧为 100 行 / 12KB，行格式不变；NOTES_SYSTEM_PROMPT 中「≤200 行且 ≤25KB」文案同步改为新上限。

## 核心数据结构

```python
# memory/prompts.py 新增
class RecallParseError(Exception)          # 召回响应解析失败（JSON 非法）

RECALL_SYSTEM_PROMPT = "..."               # 固定系统提示（约束输出协议）
def build_recall_request(query, user_index, project_index) -> str
def parse_recall_response(text) -> list[RecallHit]   # 非法抛 RecallParseError

@dataclass
class RecallHit:                           # 一条选中结果
    scope: str    # "user" | "project"（校验非法值丢弃）
    slug: str     # 笔记文件名（不含 .md）
```

LLM 输出协议（JSON）：`{"notes": [{"scope": "user", "slug": "xxx"}, ...]}`，最多 3 条，无相关输出空列表。

## 模块设计

### 模块 A：`memory/recall.py`（新建）—— RecallMemoryTool

**职责**：召回工具本体。校验 query → 读两份索引 → 组装判相关请求 → 同步收集 LLM 回复 → 解析 → 读笔记全文拼结果。

**对外接口**：`RecallMemoryTool(provider, user_dir: Path, project_dir: Path)`；类属性 `name="recall_memory"`、`read_only=True`、`timeout` 用默认 30s；`execute(args) -> ToolResult`。

**依赖**：`NoteManager`、`memory/prompts.py` 的召回模板与解析、`Provider`、`tools.base.Tool`。

### 模块 B：`memory/prompts.py`（修改）—— 召回提示与解析

**职责**：新增 `RECALL_SYSTEM_PROMPT`（约束只输出 JSON、只从给定摘要行中挑、最多 3 条）、`build_recall_request`、`parse_recall_response`（复用现有 `_extract_json` 围栏剥离模式；scope/slug 非法条目丢弃）、`RecallParseError`。另把 NOTES_SYSTEM_PROMPT 第 3 条规则文案「≤200 行且 ≤25KB」改为「≤100 行且 ≤12KB」。

### 模块 C：`memory/notes.py`（修改）—— 索引上限瘦身

**职责**：`INDEX_MAX_LINES` 200→100，`INDEX_MAX_BYTES` 25KB→12KB。`write_index` 截断逻辑不变（已按常量工作）。

### 模块 D：`cli.py`（修改）—— 装配注册

**职责**：`build_memory_context` 之后构造 `RecallMemoryTool(provider, USER_DIR / USER_MEMORY_DIR, project_root / PROJECT_MEMORY_DIR)` 并 `registry.register(...)`。注册位置在 `AgentTool` 旁（启动校验之后，不参与 `validate_tools`）。

**F4 说明**：defined 角色默认取 registry 全量工具、fork 继承父可见工具——共享 registry 注册即自动对子 Agent 可见，**无需额外代码**。工具依赖全部构造注入，不依赖 ParentContext，子 Agent 线程同样可用。

## 模块交互

```
模型 → execute_call(registry) → run_tool → RecallMemoryTool.execute(args)
  1. query 非空校验（失败 → failure）
  2. NoteManager(user_dir).index_text() / NoteManager(project_dir).index_text()
  3. 两份皆空 → success("没有找到相关记忆")（不发 LLM 调用）
  4. build_recall_request(query, user_index, project_index)
     → SystemPrompt(stable=RECALL_SYSTEM_PROMPT, dynamic="")
     → provider.stream([Message(role="user", text=request)], [], system) 收集文本
     （LLM 调用内联实现，同 NoteUpdater._call_llm 模式；本期不重构 ch11 代码）
  5. parse_recall_response(text) → hits（≤3，去重）
  6. 逐条：对应级别 NoteManager 读 <slug>.md 全文（FileNotFound 跳过）
     → 拼「## 笔记: <slug>（用户级/项目级记忆）\n<全文>」
  7. 有内容 → success(拼接)；无内容 → success("没有找到相关记忆")
异常路径：LLM 调用抛 ProviderError → failure("记忆检索失败: <原因>")（N2）
```

## 文件组织

```
mewcode/
├── memory/
│   ├── prompts.py    — 修改：+RECALL_SYSTEM_PROMPT、build_recall_request、
│   │                   parse_recall_response、RecallParseError；
│   │                   NOTES_SYSTEM_PROMPT 索引规则文案改新上限
│   ├── notes.py      — 修改：INDEX_MAX_LINES/INDEX_MAX_BYTES 收紧
│   └── recall.py     — 新建：RecallMemoryTool（模块 A）
├── cli.py            — 修改：构造并注册 RecallMemoryTool（模块 D）
tests/
└── test_memory_recall.py — 新建：提示构建/解析、工具执行（mock provider）、
                            新上限截断回归
```

## 技术决策

| 决策点 | 选择 | 理由 |
|---|---|---|
| 判相关输入 | query + 两份 index 摘要行（不读笔记全文、不读对话历史） | spec 边界；摘要行是 LLM 维护的一句话描述，足够判相关且省 token |
| 输出协议 | JSON `{"notes":[{"scope","slug"}]}` ≤3 条 | 与 NOTES 输出协议同风格，复用 `_extract_json` 围栏剥离 |
| 笔记定位 | 按 hit 直接读 `<slug>.md` | 目录即键，无需另建映射；文件缺失跳过 |
| provider 注入 | 构造注入默认 provider | UseSkillTool 同款模式；判相关是系统级辅助调用，与角色 provider 无关；provider 每次调用新建 httpx.Client，子 Agent 线程并发安全 |
| 子 Agent 可见性 | 共享 registry 注册，不写额外逻辑 | defined 默认全量、fork 继承可见工具，F4 零成本 |
| LLM 调用实现 | recall.py 内联同步 stream 收集 | 与 NoteUpdater 同模式；本期不重构 ch11，避免回归 |
| 瘦身落地 | 常量 + NOTES_SYSTEM_PROMPT 文案同步改 | LLM 按提示词控制索引内容量，硬截断只是兜底；只改常量会让 LLM 仍按旧上限生成 |
| 解析失败/无相关 | 返回「没有找到相关记忆」成功结果 | spec F2，不中断对话 |
| 两份索引皆空 | 不发 LLM 调用直接返回 | 省一次无意义调用 |
