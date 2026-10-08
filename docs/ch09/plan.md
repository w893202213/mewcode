# 上下文管理 Plan

## 架构概览

新增 `mewcode/context/` 包，四个模块各司其职；现有六处做接入式修改：

| 组件 | 职责 |
|------|------|
| `context/estimator.py` — **TokenEstimator** | 锚定最近一次 usage 的 `input_tokens` + 当时消息字符快照；增量按字符差分÷4 估算 |
| `context/store.py` — **ResultStore** | 工具结果存盘（`.mewcode/context/` 下自增编号文件）、预览文本生成 |
| `context/prompts.py` | 摘要专用 Prompt 模板（6 固定部分 + 草稿/正式两段式要求）与响应解析 |
| `context/compressor.py` — **Compressor** | 编排两层压缩：轻量预防（prevent）、重量兜底（summarize）、熔断计数、触发判定 |
| `agent/events.py`（改） | 新增 `CompressEvent`，压缩全程经事件流可见（F7） |
| `agent/loop.py`（改） | 工具结果回灌前跑轻量预防；每轮请求前跑重量判定；`Done` 事件更新估算锚点；新增 `/compact` 同步入口 |
| `session.py`（改） | `ChatSession.replace_head()`：压缩时整体替换头部消息 |
| `tui/commands.py`（改） | `/compact` 命令分发 |
| `tui/app.py`（改） | 渲染 `CompressEvent` |
| `config.py`（改） | `ProviderConfig` 增 `context_window`（默认 200000，F1） |
| `cli.py`（改） | 装配 Agent 时传入 `context_window` |

**压缩后的会话形态**（重量兜底）——摘要区用户消息原文保留，其余消息替换为摘要 + 边界消息：

```
压缩前:  U1 A1 R1 U2 A2 R2 ... Un An    （U=用户原文 A=assistant R=工具结果）
压缩后:  U1 U2 [摘要消息] [边界消息] Un An
              └── 摘要区 ──┘└── 保留区原文不动 ──┘
```

连续 user 消息协议允许（现有工具结果回灌已是连续 user 消息模式），provider 无需改动。

## 核心数据结构

### TokenEstimator

```python
class TokenEstimator:
    CHARS_PER_TOKEN = 4  # 近似比例：4 字符 ≈ 1 token

    _anchor_tokens: int | None   # 锚点：最近一次 usage.input_tokens
    _anchor_chars: int | None    # 锚点：该次请求发送的消息字符总量快照

    def anchor(self, input_tokens: int, messages: list[Message]) -> None
        # Done 事件后调用：记录 input_tokens + 当前消息字符总量
    def estimate(self, messages: list[Message], system: SystemPrompt) -> int
        # 无锚点：(消息字符 + stable + dynamic) ÷ 4
        # 有锚点：anchor_tokens + max(0, 当前消息字符 − anchor_chars) ÷ 4
        # 注：锚点已含当时系统提示与工具定义；dynamic 的轮次变化忽略（余量兜底）
    def _message_chars(m: Message) -> int
        # text + thinking + 各 tool_result.content + 各 tool_use.args 的 JSON 字符
```

用「字符总量差分」定位增量而非消息索引——压缩替换头部后消息索引失效，差分自然成立：压缩使字符总量下降，估算随之下降，无需追踪消息身份。

### ResultStore

```python
class StoreError(Exception): ...  # 存盘失败（中文消息）

class ResultStore:
    def __init__(self, base_dir: Path) -> None   # 缺省 <启动目录>/.mewcode/context/
    def save(self, content: str, seq: int) -> Path
        # 写入 tool_result_<seq>.txt（序号自增），失败抛 StoreError
    def preview(self, content: str, path: Path, head_chars: int) -> str
        # 「前 N 字符预览 + … + 文件路径 + 需要详情请读取文件」提示文本
```

### Compressor

```python
class SummarizeError(Exception): ...  # 摘要生成失败（中文消息）

@dataclass
class SummarizeResult:
    compacted_messages: int   # 被摘要掉的消息条数
    kept_user_messages: int   # 摘要区原文保留的用户消息条数
    est_saved_tokens: int     # 估算节省 token

class Compressor:
    def __init__(self, provider, estimator, store, context_window: int,
                 result_threshold: int = 12000,   # 单条工具结果阈值（字符）
                 preview_chars: int = 1000,       # 预览长度（字符）
                 auto_margin: int = 13000,        # 自动：触发线 = 窗口 − 13000
                 manual_margin: int = 3000,       # 手动：目标水位 = 窗口 − 3000
                 keep_tokens: int = 10000,        # 保留区 ≈10000 token
                 min_keep_messages: int = 5)      # 保留区至少 5 条

    def prevent(self, msg: Message) -> CompressEvent | None
        # 轻量预防：单条超阈值存盘替换；消息内多结果合计超阈值按从大到小依次存盘
        # 存盘失败降级：返回 None 不阻断（N6）
    def summarize(self, session: ChatSession, manual: bool = False) -> SummarizeResult
        # 重量兜底（同步）：选保留区 → 组装摘要请求（tools=[]）→ 同步消费
        #   provider.stream → 解析正式摘要段 → replace_head 重构会话
        # 失败抛 SummarizeError 并计数；成功清零（F6）
    def needs_auto(self, messages, system) -> bool
        # 估算总量 ≥ 窗口 − auto_margin 且未熔断
    @property
    def breaker_open(self) -> bool   # 连续失败 ≥ 3 次
```

### 事件与命令（接入点）

```python
@dataclass
class CompressEvent:
    kind: str   # "prevent" | "summarize_start" | "summarize_done" | "summarize_failed" | "breaker"
    message: str                      # 中文提示（TUI 直接展示）
    path: str | None = None           # prevent：存盘路径
    compacted_messages: int = 0       # summarize_done：压掉条数
    est_saved_tokens: int = 0         # summarize_done：估算节省
```

```python
class Agent:
    def compact_now(self) -> str
        # /compact 同步入口：无条件 summarize(manual=True)，返回中文反馈文本
        # （TUI 主线程直接调用；输入时 Agent 循环必然已结束，无并发）
```

摘要失败后的行为：抛出的 `SummarizeError` 由 `run()` 捕获 → yield 失败事件 → **按未压缩状态继续发请求**（降级不阻断，N6）；熔断后自动压缩不再发起。

## 模块设计

### context/estimator.py — TokenEstimator

**职责：** token 近似估算：锚定 usage、字符差分算增量（F2）
**对外接口：** `anchor(input_tokens, messages)`、`estimate(messages, system) -> int`
**依赖：** `session.Message`、`provider.base.SystemPrompt`（纯计算，无 IO）

### context/store.py — ResultStore

**职责：** 工具结果落盘与预览文本（F3）
**对外接口：** `save(content, seq) -> Path`、`preview(content, path, head_chars) -> str`；`StoreError`
**依赖：** 仅标准库；目录自动创建

### context/prompts.py — 摘要 Prompt 与解析

**职责：** 组装摘要请求、解析响应（F4）
**对外接口：**
- `build_summarize_request(messages_to_summarize) -> (SystemPrompt, list[Message])`——system.stable = 摘要指令（禁止调工具、先草稿后正式、6 部分模板），messages = 单条 user 消息承载待摘要内容（逐条列出角色与内容）
- `parse_summary(text) -> str`——提取 `<正式摘要>` 段；缺失/为空抛 `SummarizeError`
**依赖：** `provider.base.SystemPrompt`、`session.Message`

### context/compressor.py — Compressor

**职责：** 两层压缩编排、熔断计数、触发判定（F3/F4/F5/F6）
**对外接口：** `prevent(msg)`、`summarize(session, manual)`、`needs_auto(messages, system)`、`breaker_open`
**依赖：** Provider（复用当前实例）、TokenEstimator、ResultStore、prompts、ChatSession

### 接入修改

| 模块 | 改动 |
|------|------|
| `session.py` | `replace_head(end: int, new_messages: list[Message])`——丢弃 `messages[:end]`，头部换成新消息（压缩重构用） |
| `agent/loop.py` | 构造 Compressor；回灌后对新消息 `prevent()`；每轮请求前 `needs_auto()` → `summarize()`；`Done` 后 `estimator.anchor()`；新增 `compact_now()` |
| `agent/events.py` | 新增 `CompressEvent` 并加入 `AgentEvent` 联合类型 |
| `tui/commands.py` | `/compact` 分发 → `agent.compact_now()`，反馈文本直接显示 |
| `tui/app.py` | `_consume_events` / `_drain_cancelled` 渲染 `CompressEvent`（灰色提示行） |
| `config.py` | `ProviderConfig.context_window: int = 200000`，非正整数抛 `ConfigError`（F1） |
| `cli.py` | 装配 Agent 时传入 `context_window` |

## 模块交互

**① 轻量预防（工具结果回灌时，每轮循环内）：**

```
Agent.run → add_tool_result(新消息) → compressor.prevent(新消息)
  → 超阈值: store.save(完整内容) → 消息内替换为 preview()
  → yield CompressEvent(prevent, 路径) → yield ToolResultEvent(原文，展示不受影响)
  → 存盘失败: 降级返回 None，消息保持原文（N6）
```

**② 重量兜底（每轮 API 请求前）：**

```
Agent.run 每轮 → 组装 system → compressor.needs_auto(messages, system)?
  └─ 是且未熔断:
       yield CompressEvent(summarize_start)
       asyncio.to_thread(compressor.summarize, session, manual=False)
         内部: 尾部选保留区（≈10K token 且 ≥5 条）
             → prompts.build_summarize_request(摘要区)
             → provider.stream(摘要 messages, tools=[], 摘要 system) 同步消费
             → parse_summary 提取正式段
             → session.replace_head 重构：[摘要区用户原文] + [摘要] + [边界] + 保留区
       成功: yield CompressEvent(summarize_done, 统计)
       失败: 计数+1，yield CompressEvent(summarize_failed) → 按未压缩状态继续发请求
```

**③ 锚点更新（每轮流结束）：** `Done(usage)` → `estimator.anchor(input_tokens, messages)`

**④ 手动触发：** `/compact` → `handle_command` → `agent.compact_now()` → `summarize(manual=True)`（无条件、无视熔断、目标水位窗口−3K）→ 返回中文反馈

## 文件组织

```
mewcode/
├── context/
│   ├── __init__.py      — 导出 Compressor、TokenEstimator、ResultStore、StoreError、SummarizeError
│   ├── estimator.py     — TokenEstimator
│   ├── store.py         — ResultStore、StoreError
│   ├── prompts.py       — build_summarize_request、parse_summary
│   └── compressor.py    — Compressor、SummarizeResult、SummarizeError
├── agent/
│   ├── loop.py          — 修改：接入压缩 + compact_now
│   └── events.py        — 修改：CompressEvent
├── session.py           — 修改：replace_head
├── config.py            — 修改：context_window 字段与校验
├── cli.py               — 修改：传 context_window 给 Agent
└── tui/
    ├── commands.py      — 修改：/compact
    └── app.py           — 修改：渲染 CompressEvent
tests/
├── test_estimator.py    — 新建：锚点/增量/无锚点/字符口径
├── test_store.py        — 新建：存盘/预览/失败降级
├── test_prompts.py      — 新建：请求构造（无工具）/解析（草稿丢弃、缺正式段报错）
├── test_compressor.py   — 新建：prevent 单条与合计/保留区选择/熔断计数
├── test_context_loop.py — 新建：Agent 集成（自动触发、/compact、熔断后不发起）
└── test_config.py       — 扩展：context_window 默认与非法值
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 压缩器同步、调用方适配 | Compressor 全部同步；Agent 内 `asyncio.to_thread`、`/compact` 直接调 | `provider.stream` 本就是同步迭代器；同一套逻辑两处复用 |
| 增量定位方式 | 字符总量差分（非消息索引） | 压缩替换头部后消息索引失效；差分对「追加 / 替换头部」两类变更都成立 |
| 摘要生成方式 | 单次调用内两段式（分析草稿段 + 正式摘要段） | N2 至多一次 LLM 调用；草稿段解析后即弃（N5） |
| 摘要响应解析 | 只取 `<正式摘要>` 段；缺失/为空即失败进熔断 | 保守口径，避免半截内容进会话 |
| 保留区估算 | 逐条消息字符÷4 从尾部累加，≈10000 token 且 ≥5 条 | 与估算器同一比例，简单一致 |
| 摘要请求形态 | system.stable = 摘要指令（禁止工具 + 草稿要求）、tools = 空列表、单条 user 消息承载待摘要内容 | F4 明确禁止工具；stable 走缓存前缀无副作用 |
| 轻量预防实现点 | 回灌时对新消息就地处理，不扫描全历史 | 幂等天然成立（N4）；每次只碰新内容 |
| 存盘位置 | `<启动目录>/.mewcode/context/`，`tool_result_<seq>.txt` | 与项目级配置目录一致、路径可预测，摘要/读取提示可指向真实文件 |
| 熔断恢复 | 成功一次即清零；`/compact` 无视熔断总是允许 | F6 原文；给用户人工恢复通道 |
| 摘要输入与轻量衔接 | 摘要输入直接用会话里的消息（含已替换的预览+路径） | 不重复读盘；两层压缩自然衔接 |
| 摘要输出上限 | 沿用 provider 默认 `max_tokens`（8192） | 摘要输出远小于该值，不加新配置 |
| context_window 校验 | 非正整数抛 `ConfigError` | fail-closed 口径与现有配置一致（F1） |
