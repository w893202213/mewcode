# 结构化系统提示 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `mewcode/prompt/__init__.py` | 导出 Environment/SwitchDirective/SystemPromptBuilder/PLAN_MODE_DIRECTIVE |
| 新建 | `mewcode/prompt/modules.py` | 七个固定模块的标题与全文常量 |
| 新建 | `mewcode/prompt/builder.py` | Environment、SwitchDirective、SystemPromptBuilder、build_supplement |
| 修改 | `mewcode/provider/base.py` | 新增 SystemPrompt；UsageInfo 加缓存字段；Provider.stream 加 system 参数 |
| 修改 | `mewcode/provider/anthropic.py` | system 块列表＋缓存断点；message_start/message_delta 缓存字段解析 |
| 修改 | `mewcode/provider/openai.py` | system 角色消息（stable 在前）；prompt_cache_hit 解析 |
| 修改 | `mewcode/agent/events.py` | UsageEvent 加缓存字段 |
| 修改 | `mewcode/agent/loop.py` | 删 Plan 会话消息；每轮组装 SystemPrompt；累计缓存 token |
| 修改 | `mewcode/config.py` | 新增 load_custom_instructions |
| 修改 | `mewcode/cli.py` | 读取并传递 custom_instructions |
| 修改 | `mewcode/tui/app.py` | run() 透传；用量行显示缓存命中/写入 |
| 修改 | `mewcode/tools/files.py`、`search.py`、`shell.py` | 六个工具描述补双重强化规则句 |
| 修改 | `config.example.yaml` | custom_instructions 示例字段 |
| 新建 | `tests/test_prompt.py` | 拼装顺序、原文语义、补充块、注入节奏单测 |
| 修改 | `tests/test_providers.py` | stream 新签名；system/断点/缓存字段断言 |
| 修改 | `tests/test_provider_tools.py` | _build_payload 新签名调用点 |
| 修改 | `tests/test_agent_loop.py` | FakeProvider 新签名 |
| 修改 | `tests/test_plan_mode.py` | 断言迁移：会话不再写消息，改为捕获 system 注入 |
| 修改 | `tests/test_config.py` | custom_instructions 读取断言 |

## T1: Provider 基础类型与签名扩展

**文件：** `mewcode/provider/base.py`
**依赖：** 无
**步骤：**
1. 新增 `SystemPrompt` 数据类（stable/dynamic 两字段，带中文注释）
2. `UsageInfo` 加 `cache_hit_tokens: int | None = None` 与 `cache_write_tokens: int | None = None`
3. `Provider.stream` 抽象方法签名加 `system: SystemPrompt` 参数并更新 docstring

**验证：** `python -m compileall mewcode/` 通过；`python -m pytest tests/test_session.py tests/test_config.py -q` 通过（未受影响）

## T2: 七个固定模块文本

**文件：** `mewcode/prompt/modules.py`
**依赖：** 无
**步骤：**
1. 定义 `Module` 数据类（title、body 两字段）
2. 按 plan.md「七个固定模块文本」原文定义七个常量，顺序即优先级：身份、系统约束、任务模式、动作执行、工具使用、语气风格、文本输出
3. 定义 `MODULES: tuple[Module, ...]` 与 `STABLE_TEXT: str`（七模块拼接，`## title` + 正文，模块间空行）

**验证：** `python -c "from mewcode.prompt.modules import MODULES, STABLE_TEXT; print(len(MODULES), len(STABLE_TEXT))"` 输出 `7` 与约 900+ 字符的文本长度

## T3: 拼装器（Environment / SwitchDirective / SystemPromptBuilder）

**文件：** `mewcode/prompt/builder.py`
**依赖：** T1、T2
**步骤：**
1. `Environment` 数据类（cwd、platform）
2. `SwitchDirective` 数据类（name、full_text、slim_text、repeat_every=4）与 `text_for(iteration)`：`iteration == 1 or iteration % repeat_every == 0` → full_text，否则 slim_text
3. `build_supplement(env, directive_texts, date) -> str`：生成 `<supplement>` 块——【环境信息】小节（工作目录/操作系统/当前日期三条）+【会话状态】小节（directive_texts 非空时逐条列出）
4. `SystemPromptBuilder.__init__(env, custom_instructions="")`；`build(directives, iteration)` 返回 `SystemPrompt(stable=STABLE_TEXT, dynamic=supplement + 自定义指令块)`——自定义指令非空时以 `【自定义指令】` 标题块跟在补充块后；日期每次 build 现取 `date.today()`（可注入便于测试）

**验证：** `python -m compileall mewcode/prompt/` 通过

## T4: prompt 包导出

**文件：** `mewcode/prompt/__init__.py`
**依赖：** T3
**步骤：**
1. 导出 Environment、SwitchDirective、SystemPromptBuilder、PLAN_MODE_DIRECTIVE（Plan Mode 的完整/精简文本常量，完整版＝ch05 原文语义，精简版＝「Plan Mode 开启中：仅只读工具」）

**验证：** `python -c "from mewcode.prompt import Environment, SwitchDirective, SystemPromptBuilder, PLAN_MODE_DIRECTIVE; print(PLAN_MODE_DIRECTIVE.full_text[:20])"` 正常输出

## T5: Anthropic provider——system 块、缓存断点与缓存字段解析

**文件：** `mewcode/provider/anthropic.py`、`tests/test_providers.py`、`tests/test_provider_tools.py`
**依赖：** T1
**步骤：**
1. `_build_payload` 加 `system: SystemPrompt` 参数：组装 system 块列表——`[{"type":"text","text":stable,"cache_control":{"type":"ephemeral"}}]`，dynamic 非空时追加 `{"type":"text","text":dynamic}`（不带断点）；stable 为空的退化处理（如测试调用）不加断点
2. tools 非空时最后一个工具加 `"cache_control": {"type": "ephemeral"}`
3. `stream()` 签名加 system 并透传
4. `_iter_events`：新增解析 `message_start` 事件——`usage` 的 `input_tokens`/`cache_creation_input_tokens`/`cache_read_input_tokens`；`message_delta` 的 `output_tokens` 合并；`Done.usage` 携带归一化缓存字段
5. 更新 `tests/test_providers.py`：所有 anthropic 的 `stream` 调用点补 system 参数；`ANTHROPIC_EVENTS` 加 message_start 携带 usage（含缓存字段）；新增断言——请求体 system 为块列表、stable 块带 cache_control、最后一个工具带 cache_control、Done.usage 缓存字段正确
6. 更新 `tests/test_provider_tools.py`：`_build_payload` 调用点补 system 参数

**验证：** `python -m pytest tests/test_providers.py tests/test_provider_tools.py -q` 全绿

## T6: OpenAI provider——system 消息与缓存字段解析

**文件：** `mewcode/provider/openai.py`、`tests/test_providers.py`、`tests/test_provider_tools.py`
**依赖：** T1
**步骤：**
1. `_build_payload` 加 `system: SystemPrompt` 参数：消息列表头部插 `{"role": "system", "content": stable + ("\n\n" + dynamic if dynamic else "")}`（stable 在最前）
2. usage 解析：`prompt_cache_hit_tokens` → `cache_hit_tokens`；`prompt_cache_miss_tokens` 解析但丢弃（不映射）
3. `stream()` 签名加 system 并透传
4. 更新 `tests/test_providers.py`：openai 调用点补 system 参数；`OPENAI_CHUNKS` 最终 chunk 加 usage（含 prompt_cache_hit_tokens）；新增断言——messages[0] 为 system 角色且 stable 在前、Done.usage 缓存命中正确
5. 更新 `tests/test_provider_tools.py` 的 openai 调用点

**验证：** `python -m pytest tests/test_providers.py tests/test_provider_tools.py -q` 全绿

## T7: UsageEvent 扩展

**文件：** `mewcode/agent/events.py`
**依赖：** T1
**步骤：**
1. `UsageEvent` 加 `cache_hit_tokens: int | None = None`、`cache_write_tokens: int | None = None`（默认值保证旧构造仍可用）

**验证：** `python -m compileall mewcode/agent/` 通过

## T8: Agent 迁移 Plan 注入并接线系统提示

**文件：** `mewcode/agent/loop.py`、`tests/test_agent_loop.py`、`tests/test_plan_mode.py`
**依赖：** T3、T4、T7
**步骤：**
1. 删除 `PLAN_MODE_ON_MESSAGE`/`PLAN_MODE_OFF_MESSAGE` 常量；`enter_plan_mode`/`exit_plan_mode` 只置/清 `_plan_mode` 标志（去掉 `add_user`）
2. `Agent.__init__` 加 `custom_instructions: str = ""` 参数；创建 `Environment`（cwd=os.getcwd() 包 try/except 失败用「未知」、platform=platform.platform()）与 `SystemPromptBuilder`
3. `run()` 每轮迭代：`directives = [PLAN_MODE_DIRECTIVE] if self._plan_mode else []`；`system = self._builder.build(directives, iteration)`；`_stream_bridged`/`_pump_provider` 透传 system
4. 轮内累计 cache_hit/cache_write token（Done.usage 的缓存字段），`UsageEvent` 随用量事件携带累计值
5. 更新 `tests/test_agent_loop.py`：FakeProvider.stream 加 system 参数
6. 更新 `tests/test_plan_mode.py`：enter/exit 后 `session.messages` 保持为空（不再有 ON/OFF 消息）；FakeProvider/ScriptedProvider 捕获每次 stream 的 system——断言 plan 模式下 dynamic 含「Plan Mode」文本、exit 后不含；多轮流断言第 1 轮完整版、第 2 轮精简版

**验证：** `python -m pytest tests/test_agent_loop.py tests/test_plan_mode.py -q` 全绿

## T9: custom_instructions 配置读取

**文件：** `mewcode/config.py`、`tests/test_config.py`、`config.example.yaml`
**依赖：** 无
**步骤：**
1. `config.py` 新增 `load_custom_instructions(path) -> str`：读 YAML 顶层 `custom_instructions` 字段；文件缺失/字段缺失/非字符串一律返回空串；文件本身损坏时抛 ConfigError（与 load_config 一致）
2. `tests/test_config.py` 新增：填写时返回原文（多行）、不填时返回空串
3. `config.example.yaml` 加带注释的示例字段

**验证：** `python -m pytest tests/test_config.py -q` 全绿

## T10: CLI 与 TUI 接线

**文件：** `mewcode/cli.py`、`mewcode/tui/app.py`
**依赖：** T8、T9
**步骤：**
1. `cli.py`：读 custom_instructions 并传入 `tui.run(provider, config, registry, custom_instructions)`
2. `tui/app.py`：`run()` 签名加 custom_instructions 并传给 Agent；usage 字典加 cache 键；`_print_end` 用量行在缓存命中非空时追加「 · 缓存命中 X tokens」、缓存写入非空时追加「 · 缓存写入 Y tokens」

**验证：** `python -m compileall mewcode/` 通过；`python -m pytest -q` 全绿（无 TUI 单测，行为留 T14 e2e 观察）

## T11: 工具描述双重强化

**文件：** `mewcode/tools/files.py`、`mewcode/tools/search.py`、`mewcode/tools/shell.py`
**依赖：** 无（与 T2 文本呼应）
**步骤：**
1. `read_file` 描述补：「读文件请用本工具，不要用 shell 的 cat/sed」
2. `edit_file` 描述补：「修改文件前必须先用 read_file 读取当前内容，确保 old_text 与文件一致；old_text 必须唯一匹配」
3. `write_file` 描述补：「覆盖写入前先确认目标内容」
4. `find_files`/`grep_code` 描述补：「找文件/搜代码请用本工具，不要用 shell 的 find/grep」
5. `run_command` 描述补：「仅当专用工具（read_file/write_file/edit_file/find_files/grep_code）覆盖不到的需求才使用本工具」

**验证：** `python -m pytest tests/test_tools.py tests/test_registry.py -q` 全绿；`grep -c "read_file" mewcode/tools/shell.py` 输出 ≥1

## T12: prompt 拼装单测

**文件：** `tests/test_prompt.py`
**依赖：** T3
**步骤：**
1. 模块顺序断言：STABLE_TEXT 中七个标题按 身份→系统约束→任务模式→动作执行→工具使用→语气风格→文本输出 顺序出现
2. 三行原文语义断言：STABLE_TEXT 含「MewCode」「终端」「阅读代码」「编写代码」「调试」「先思考再行动」
3. 自定义指令：builder 带 custom 时 dynamic 含【自定义指令】与原文、不带时不出现
4. `build_supplement`：含 `<supplement>` 标签、【环境信息】与 cwd/platform/date 三行、指令文本
5. `SwitchDirective.text_for`：iteration 1→完整、2/3→精简、4→完整、5/6/7→精简；repeat_every 可自定义
6. `SystemPromptBuilder.build`：两次 build 的 stable 相同、dynamic 随 iteration/指令变化

**验证：** `python -m pytest tests/test_prompt.py -q` 全绿

## T13: 全量回归

**文件：** 无
**依赖：** T5-T12
**步骤：**
1. `python -m pytest -q` 全部通过（旧测试已随各任务更新）

**验证：** 输出 `N passed`、无 failure

## T14: tmux 端到端

**文件：** 无
**依赖：** T13
**步骤：**
1. `scripts/mock_llm.py` 起的 mock 服务下 tmux 启动 MewCode 对话（或真实 API）
2. 按 checklist 端到端场景走查：多轮编辑、Plan Mode、缓存字段展示
3. 检查 `mock_requests.log`（如使用）中请求体含 system 块与 cache_control 断点

**验证：** 对照 checklist 逐项记录结果

## 执行顺序

```
T1 ──┬── T5 ──┐
     │        ├── T7 ── T8 ── T10
T2 ──┴── T6 ──┘              │
     │                       │
     └── T3 ── T4 ───────────┼── T12 ── T13 ── T14
                             │
T9（可并行）─────────────────┘
T11（可并行，任意时机）
```

说明：T5/T6 前 `test_providers.py` 等既有测试保持绿色（T1 只改抽象签名与类型，不改实现）；T5/T6/T8 各自在任务内同步更新受影响的测试，避免长时间红灯。
