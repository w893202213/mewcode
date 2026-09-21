# 结构化系统提示 Plan

## 架构概览

新增一个 **`prompt` 包**专职系统提示，其余改动分布在各层：

1. **`prompt` 包（新建）**——系统提示的唯一生产者
   - `modules.py`：七个固定模块的完整文本（常量，纯文本，无逻辑）
   - `builder.py`：`Environment`（环境信息快照）、`SwitchDirective`（会话级开关指令＋注入节奏）、`SystemPromptBuilder`（把模块、补充块、自定义指令拼成一次请求的系统提示）
   - 拼装是纯函数过程，不依赖网络与界面，可独立单测

2. **Provider 层（修改）**——协议映射与缓存落地
   - `Provider.stream` 签名增加系统提示参数；`SystemPrompt` 类型（stable/dynamic 两部分）定义在 provider 层
   - Anthropic：stable 块尾部与最后一个工具加 `cache_control: ephemeral` 断点；dynamic 作为断点后的 system 补充块
   - OpenAI：system 角色消息＝stable 在前＋dynamic 在后（依赖服务端自动前缀缓存）；无显式断点
   - 两家 provider 都扩展 usage 解析：缓存命中/写入字段归一化进 `UsageInfo`

3. **Agent 层（修改）**——每轮动态注入的驱动者
   - 持有 `SystemPromptBuilder` 与活跃的开关指令（当前仅 Plan Mode）
   - 每轮迭代计算指令文本（首轮完整/每 4 轮完整/其余精简），组装 `SystemPrompt` 交给 provider
   - 删除 ch05 的 `PLAN_MODE_ON/OFF` 会话 user 消息；`/plan`、`/do` 只切换开关标志

4. **配置与 CLI（修改）**——`custom_instructions` 数据源
   - config.yaml 顶层新增可选字段；config 层提供读取函数；CLI 读取后传进 TUI/Agent

5. **TUI（修改）**——缓存效果可观测
   - 用量行追加「缓存命中 X tokens」（服务端返回写入字段时也展示「缓存写入 Y tokens」）

6. **工具描述（修改）**——F5 双重强化
   - `read_file`/`write_file`/`edit_file`/`find_files`/`grep_code`/`run_command` 的描述里补上与全局指令呼应的规则句

## 核心数据结构

### SystemPrompt（provider/base.py 新增）

```python
@dataclass
class SystemPrompt:
    """一次请求的系统提示：stable 进入可缓存前缀，dynamic 紧随其后、不缓存。"""
    stable: str   # 七个固定模块文本
    dynamic: str  # <supplement>补充块 + 自定义指令块（都无则空串）
```

### UsageInfo 扩展（provider/base.py 修改）

```python
@dataclass
class UsageInfo:
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int | None = None   # 命中的缓存读取量（Anthropic cache_read / OpenAI prompt_cache_hit）
    cache_write_tokens: int | None = None # 本次写入缓存的量（Anthropic cache_creation；OpenAI 无对应字段）
```

### Provider 接口（provider/base.py 修改）

```python
class Provider(ABC):
    @abstractmethod
    def stream(
        self, messages: list[Message], tools: list[Tool], system: SystemPrompt
    ) -> Iterator[StreamEvent]:
        ...
```

### Environment（prompt/builder.py 新增）

```python
@dataclass
class Environment:
    """环境信息快照：启动时采样 cwd 与平台，日期在每次构建补充块时现取。"""
    cwd: str       # MewCode 启动目录（绝对路径，与 shell 工具语义一致）
    platform: str  # 如 "Linux 6.6.87.2-microsoft-standard-WSL2"
```

### SwitchDirective（prompt/builder.py 新增）

```python
@dataclass
class SwitchDirective:
    """一个会话级开关的指令与注入节奏。"""
    name: str
    full_text: str     # 完整版（首轮与周期轮注入）
    slim_text: str     # 精简版（其余轮注入）
    repeat_every: int = 4   # 每隔 N 轮重复完整版

    def text_for(self, iteration: int) -> str:
        """第 iteration 轮注入哪一版：首轮或 iteration % repeat_every == 0 → 完整，否则精简。"""
```

### SystemPromptBuilder（prompt/builder.py 新增）

```python
class SystemPromptBuilder:
    """拼装一次请求的系统提示。构造时固定 env 与自定义指令；每次 build 现取日期。"""
    def __init__(self, env: Environment, custom_instructions: str = "") -> None: ...
    def build(self, directives: list[SwitchDirective], iteration: int) -> SystemPrompt:
        """返回 SystemPrompt：stable=七模块；dynamic=<supplement>（环境信息+指令文本）+自定义指令块。"""
```

### config 层新增（config.py 修改）

```python
def load_custom_instructions(path: str | os.PathLike) -> str:
    """读取 YAML 顶层可选字段 custom_instructions（多行文本）；缺省或非字符串返回空串。"""
```

### UsageEvent 扩展（agent/events.py 修改）

```python
@dataclass
class UsageEvent:
    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int | None = None    # 本轮累计缓存命中
    cache_write_tokens: int | None = None  # 本轮累计缓存写入
```

## 模块设计

### 模块 A：prompt 包（新建）

**职责：** 七个固定模块文本常量；环境信息、开关指令、补充块与完整系统提示的纯函数拼装。

**对外接口：**
- `MODULES: tuple[Module, ...]`——固定模块按优先级排列（每个 Module：title + 正文）
- `PLAN_MODE_DIRECTIVE: SwitchDirective`——Plan Mode 的完整/精简文本与节奏（常量）
- `Environment` / `SwitchDirective` / `SystemPromptBuilder`（见上）
- `build_supplement(env, directive_texts, date) -> str`——生成 `<supplement>` 块（纯函数，可单测）

**依赖：** 仅标准库；从 `provider.base` 导入 `SystemPrompt` 类型（构造返回值）。

**文件组织：**

```
mewcode/
├── prompt/
│   ├── __init__.py      — 导出 Environment/SwitchDirective/SystemPromptBuilder/PLAN_MODE_DIRECTIVE
│   ├── modules.py       — 七个固定模块的标题与全文（Module 常量，优先级顺序即排列顺序）
│   └── builder.py       — Environment、SwitchDirective、SystemPromptBuilder、build_supplement
├── provider/
│   ├── base.py          — 新增 SystemPrompt；UsageInfo 加缓存字段；Provider.stream 加 system 参数
│   ├── anthropic.py     — system 块列表组装（stable 块带断点 + dynamic 块）；最后一个工具带断点；
│   │                      解析 message_start/message_delta 的 cache_creation/cache_read 字段
│   └── openai.py        — 消息列表头部插 system 角色消息（stable 在前）；解析 prompt_cache_hit/miss
├── agent/
│   ├── events.py        — UsageEvent 加缓存字段
│   └── loop.py          — 删 PLAN_MODE_ON/OFF 消息注入；构造 builder；每轮 build SystemPrompt 传给 provider
├── config.py            — 新增 load_custom_instructions
├── cli.py               — 读取 custom_instructions 并传入 TUI
├── tui/
│   └── app.py           — run() 接收 custom_instructions 透传给 Agent；用量行显示缓存命中/写入
└── tools/
    ├── files.py         — read_file/write_file/edit_file 描述补双重强化规则句
    ├── search.py        — find_files/grep_code 描述同上
    └── shell.py         — run_command 描述同上
```

### 模块 B：Agent 改动细节（loop.py）

- 删掉 `PLAN_MODE_ON_MESSAGE` / `PLAN_MODE_OFF_MESSAGE` 常量与 `add_user` 调用
- `enter_plan_mode()` / `exit_plan_mode()` 只置/清 `_plan_mode` 标志（不再写会话）
- 新增：构造时接收 `custom_instructions`，创建 `Environment`（启动目录＋平台）与 `SystemPromptBuilder`；每轮迭代 `directives = [PLAN_MODE_DIRECTIVE] if self._plan_mode else []`，`system = builder.build(directives, iteration)`，`_stream_bridged` 透传 system
- 轮内累计缓存命中/写入 token，`UsageEvent` 随既有用量事件一起发

### 模块 C：补充块文本形态（Anthropic system 块 / OpenAI 系统消息尾部一致）

```
<supplement>
【环境信息】
- 工作目录: /home/user/project
- 操作系统: Linux 6.6.87.2-microsoft-standard-WSL2
- 当前日期: 2026-09-21

【会话状态】
（Plan 指令完整版或精简版，无开关时整节省略）
</supplement>
```

自定义指令块（配置了才出现）跟在补充块之后：

```
【自定义指令】
（config.yaml 的 custom_instructions 原文）
```

## 模块交互

```
启动:
  cli ──load_custom_instructions(config.yaml)──▶ custom_instructions
  cli ──▶ tui.run(provider, config, registry, custom_instructions)
  tui ──▶ Agent(..., custom_instructions)
  Agent ──采样 cwd/platform──▶ Environment ──▶ SystemPromptBuilder

每轮迭代（Agent Loop 内）:
  Agent: directives = [PLAN_MODE_DIRECTIVE] 若计划模式，否则 []
  Agent ──▶ builder.build(directives, iteration)
        ├─ stable = 七模块文本（每轮完全相同）
        └─ dynamic = <supplement>(环境信息现取日期 + 指令文本) + 自定义指令块
  Agent ──▶ provider.stream(messages, tools, SystemPrompt)
  Anthropic: system=[stable块+断点, dynamic块]；tools 最后一个加断点
  OpenAI:   messages=[{role:"system", content: stable+dynamic}] + 历史
  provider ──usage 解析──▶ UsageInfo(含缓存字段) ──▶ UsageEvent ──▶ TUI 用量行
```

请求体在连续轮次间：stable 前缀逐字节一致（缓存命中），dynamic 仅随 iteration/开关变化。

## 七个固定模块文本（常量）

模块标题用二级 Markdown 标题（`##`），模块间空行分隔。

**## 身份**
```
你是 MewCode，一个运行在终端环境中的 AI 编程助手。
你擅长阅读代码、编写代码和调试问题，能借助工具直接操作文件系统与 shell，独立完成多步任务。
工作语言为中文：回复、说明与注释均使用中文（代码标识符与专业术语除外）。
```

**## 系统约束**
```
先思考再行动：动手之前想清楚目标、方案与验证方式；推理在思考过程中完成，正式回复只给结论与关键理由，不重复整段推理。
诚实第一：不编造工具结果，不确定时明确说不知道；遇到错误如实报告，不粉饰。
尊重用户：用户否定你的方案时，调整思路而不是固执己见。
安全边界：破坏性操作（删除、覆盖、强推等）动手前先说明影响并谨慎执行。
```

**## 任务模式**
```
你以 ReAct 模式工作：思考 → 调用工具 → 观察结果 → 再思考，直到任务完成或无法继续。
多步任务自主推进：读文件、分析、修改、验证一气呵成，不要每步停下来问用户。
当前每轮回复最多迭代 30 轮，达到上限会自动停止。
```

**## 动作执行**
```
编辑前必先读：修改任何文件之前，先用 read_file 读取该文件的当前内容，确保替换文本与文件里的一字不差。
一次改一处：edit_file 的 old_text 必须在文件中唯一匹配；不唯一就提供更长的上下文。
改完要验证：修改代码后运行相关测试或命令确认结果，并把结果告诉用户。
```

**## 工具使用**
```
优先使用专用工具，不要用 shell 绕路：
- 读文件用 read_file，不要用 cat/sed；
- 写文件用 write_file，改文件用 edit_file，不要用重定向或 heredoc；
- 找文件用 find_files，搜代码用 grep_code，不要用 shell 的 find/grep；
- 只有专用工具覆盖不到的需求才用 run_command。
调用工具前检查参数：路径、内容、唯一性都对得上再动手。
```

**## 语气风格**
```
语气专业、直接、友好：不奉承、不说教、不啰嗦，用「你」称呼用户。
结论先行：先给出结果或判断，再展开必要的过程与理由。
```

**## 文本输出**
```
回复使用 Markdown 排版（标题、列表、代码块），适配终端渲染。
简洁优先：能一句说清的不写一段，不重复用户已知的信息。
代码块标注语言，方便语法高亮。
```

全模块合计约 900 汉字，加上六个工具的描述与参数 schema，稳定前缀预计超过 Anthropic 缓存断点的 1024 tokens 最小长度（以实际观测的缓存字段为准）。

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 补充通道 | 系统通道：Anthropic system 块列表 / OpenAI system 角色消息 | 用户已确认；动态内容不打断缓存前缀，模型不会当用户输入回复 |
| SystemPrompt 两段式 | `stable` + `dynamic` 两个字符串，provider 各自映射 | 协议无关的最小契约：Anthropic 拆块加断点，OpenAI 直接拼接 |
| 自定义指令位置 | 放 dynamic 尾部（断点之后），不放 stable | 保持「七模块→环境信息→自定义指令」的结构顺序；文本通常很短，不缓存代价可忽略 |
| 缓存断点 | Anthropic：stable 块尾 + 最后一个工具；OpenAI：不加显式参数 | Anthropic 需显式断点才有缓存；OpenAI/DeepSeek 自动前缀缓存，加协议外参数反而有害 |
| 注入节奏 | `repeat_every=4`：第 1、4、8…轮完整，其余精简 | spec 已确认；常量在 SwitchDirective 上，可改 |
| Plan 迁移 | 删会话 user 消息，改每轮 directives | spec 已确认；会话历史干净，注入频率可控 |
| 日期采样 | 每次 build 现取 `date.today()`；cwd/platform 启动时采样 | 跨天会话日期不失真；cwd 与 shell 工具的启动目录语义一致 |
| 缓存字段归一化 | `cache_hit_tokens`（读）/`cache_write_tokens`（写） | 两家协议字段名不同，归一化后 TUI 与事件层零协议感知 |
| Anthropic usage 来源 | 同时解析 `message_start` 与 `message_delta` | 流式响应中 input/cache 字段在 message_start 携带、output 在 message_delta；两者合并 |
| OpenAI miss 字段 | 解析但不入 UsageInfo（无对应语义） | 归一化后无「未命中」展示需求；不增加无用字段 |
| 环境信息采样失败 | 无法获取 cwd 时用「未知」占位，不中断启动 | 补充块是辅助信息，不能因它让程序挂掉 |
| 最小缓存长度 | 七个模块写足篇幅（稳定前缀含工具描述 ≥1024 tokens 目标）；若服务端忽略断点，以缓存字段观测为准 | Anthropic 断点有最小前缀长度要求；内容质量优先，观测兜底 |
