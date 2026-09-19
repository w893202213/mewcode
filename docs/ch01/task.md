# MewCode 对话 TUI（第一步）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `pyproject.toml` | 项目元数据、依赖声明 |
| 新建 | `mewcode/__init__.py` | 版本号 |
| 新建 | `mewcode/config.py` | ProviderConfig、ConfigError、load_config、select_profile |
| 新建 | `mewcode/session.py` | Message、ChatSession |
| 新建 | `mewcode/provider/__init__.py` | 注册表、create_provider |
| 新建 | `mewcode/provider/base.py` | Provider ABC、StreamEvent、ProviderError |
| 新建 | `mewcode/provider/anthropic.py` | AnthropicProvider |
| 新建 | `mewcode/provider/openai.py` | OpenAIProvider |
| 新建 | `mewcode/tui/__init__.py` | 占位 |
| 新建 | `mewcode/tui/app.py` | TUI 主循环、流式渲染、错误显示 |
| 新建 | `mewcode/tui/commands.py` | 斜杠命令 |
| 新建 | `mewcode/cli.py` | argparse、装配、启动 |
| 新建 | `mewcode/__main__.py` | `python -m mewcode` 入口 |
| 新建 | `tests/test_config.py` | 配置层测试 |
| 新建 | `tests/test_session.py` | 会话层测试 |
| 新建 | `tests/test_providers.py` | Provider 测试（本地 mock SSE） |
| 新建 | `config.example.yaml` | 示例配置（api_key 占位） |

## T1: 项目骨架

**文件：** `pyproject.toml`、`mewcode/__init__.py`、`mewcode/provider/__init__.py`（空占位）、`mewcode/tui/__init__.py`（空占位）
**依赖：** 无
**步骤：**
1. 写 `pyproject.toml`：项目名 `mewcode`，`requires-python >= 3.10`，依赖 `prompt_toolkit`、`rich`、`httpx`、`PyYAML`，dev 依赖 `pytest`；入口 `mewcode = "mewcode.cli:main"`
2. 创建包目录与三个 `__init__.py`（`mewcode/__init__.py` 里定义 `__version__ = "0.1.0"`）
3. 安装：`pip install -e ".[dev]"`

**验证：** `python -c "import mewcode; print(mewcode.__version__)"` 输出 `0.1.0`

## T2: 配置层

**文件：** `mewcode/config.py`
**依赖：** T1
**步骤：**
1. 定义 `ConfigError(Exception)`，消息一律中文、可读
2. 定义 `ProviderConfig` dataclass：`name` / `protocol` / `model` / `base_url` / `api_key` / `thinking: bool = False`
3. 实现 `load_config(path) -> list[ProviderConfig]`：YAML 顶层结构为 `providers:` 列表；逐份校验：name/protocol/model/base_url/api_key 必填、protocol ∈ {anthropic, openai}、thinking 为布尔；任何问题抛 `ConfigError` 并指明第几份配置缺什么
4. 实现 `select_profile(configs, name=None) -> ProviderConfig`：name 为 None 取第一份；找不到抛 `ConfigError` 并列出全部可用 name

**验证：** 写 `/tmp/mew_test.yaml`（两份配置），`python -c` 加载断言字段；删掉某字段再加载断言抛 `ConfigError`

## T3: 配置层测试

**文件：** `tests/test_config.py`
**依赖：** T2
**步骤：** 用 `pytest` + `tmp_path` 写用例：正常加载两份；缺字段报错；protocol 非法报错；thinking 缺省为 False；select_profile 命中、缺省取第一份、未找到报错
**验证：** `python -m pytest tests/test_config.py` 全部通过

## T4: 会话层

**文件：** `mewcode/session.py`
**依赖：** T1
**步骤：**
1. 定义 `Message` dataclass：`role: str`、`text: str`、`thinking: str = ""`、`thinking_raw: list[dict] = field(default_factory=list)`
2. 定义 `ChatSession`：`messages: list[Message]`；`add_user(text)`；`add_assistant(text, thinking, thinking_raw)`；`clear()`
**验证：** `python -c` 增两条消息、clear 后长度为 0

## T5: 会话层测试

**文件：** `tests/test_session.py`
**依赖：** T4
**步骤：** 用例：add_user/add_assistant 顺序与内容正确；clear 清空；thinking 字段存储正确
**验证：** `python -m pytest tests/test_session.py` 全部通过

## T6: Provider 抽象

**文件：** `mewcode/provider/base.py`
**依赖：** T4（类型引用）
**步骤：**
1. 定义 `ThinkingDelta(text)`、`TextDelta(text)`、`Done()` 三个 dataclass
2. 定义 `ProviderError(Exception)`
3. 定义 `Provider(ABC)`：抽象方法 `stream(self, messages: list[Message]) -> Iterator[StreamEvent]`
**验证：** `python -m compileall mewcode/` 通过；`python -c` 确认 `Provider()` 实例化报 TypeError（ABC 生效）

## T7: AnthropicProvider

**文件：** `mewcode/provider/anthropic.py`
**依赖：** T6
**步骤：**
1. 模块常量：`DEFAULT_MAX_TOKENS = 8192`、`THINKING_BUDGET = 4096`、`API_VERSION = "2023-06-01"`
2. 实现 `_build_payload(config, messages)`：`model`、`max_tokens`、`stream: true`、`messages`（user 为纯文本 content；assistant 为 content 数组：`thinking_raw` 非空时先原样放思考块，再放 text 块）；`config.thinking` 为真时加 `thinking: {"type": "enabled", "budget_tokens": THINKING_BUDGET}`；不设 temperature
3. 实现 `_parse_sse_line(line) -> dict | None`：解析 `event:` / `data:` 行返回 JSON dict
4. 实现 `AnthropicProvider.stream()`：`httpx.Client.stream("POST", base_url + "/v1/messages", headers={"x-api-key": ..., "anthropic-version": API_VERSION}, json=payload)`；逐行迭代：`content_block_delta` 的 `thinking_delta` → ThinkingDelta、`text_delta` → TextDelta；`message_stop` → Done 结束；`error` 事件 → ProviderError（含错误信息）；非 200 → ProviderError（读响应体中的错误描述）
**验证：** `python -c` 喂构造的 SSE 行序列给 `_parse_sse_line` + 事件分派逻辑，断言事件顺序；断言 `_build_payload` 中 thinking 块回传与 thinking 参数

## T8: OpenAIProvider

**文件：** `mewcode/provider/openai.py`
**依赖：** T6
**步骤：**
1. 实现 `_build_payload(config, messages)`：`model`、`messages`（仅 `role` + `content` 文本，忽略 thinking/thinking_raw）、`stream: true`；`config.thinking` 为真时加 `"thinking": true`
2. 实现 `OpenAIProvider.stream()`（结构对齐 T7）：`POST base_url + "/v1/chat/completions"`、`Authorization: Bearer`；`data:` 行 JSON 中 `choices[0].delta.content` → TextDelta；非空 `finish_reason` 或 `[DONE]` → Done；HTTP 错误 / `error` 字段 → ProviderError
**验证：** 同 T7 样式：喂构造 SSE 行断言事件序列；断言 payload 中 thinking 透传、messages 仅含 role+content

## T9: Provider 工厂

**文件：** `mewcode/provider/__init__.py`
**依赖：** T7、T8
**步骤：**
1. `_REGISTRY: dict[str, type[Provider]] = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}`
2. `create_provider(config) -> Provider`：按 `config.protocol` 查注册表实例化；未知协议抛 `ProviderError`
**验证：** `python -c` 对两家配置创建实例；未知协议断言报错

## T10: Provider 测试（mock SSE）

**文件：** `tests/test_providers.py`
**依赖：** T9
**步骤：**
1. 用 `http.server` + `threading` 起本地 mock 服务，两个端点：
   - anthropic：先回 `content_block_start`（thinking 块），再多个 `content_block_delta`（thinking_delta 若干 + text_delta 若干），最后 `message_stop`；同时记录收到的请求体供断言
   - openai：多个 chunk（`delta.content`）+ 末尾 `finish_reason: stop` 与 `[DONE]`；同样记录请求体
2. 用例：事件序列与文本拼接正确；anthropic 请求体中 thinking 参数存在（thinking=true）且 assistant 消息回传 thinking 块（对应 AC11 基础）；openai 请求体透传 `"thinking": true`；mock 返回 `error` 事件时抛 ProviderError
**验证：** `python -m pytest tests/test_providers.py` 全部通过

## T11: 斜杠命令

**文件：** `mewcode/tui/commands.py`
**依赖：** T4
**步骤：**
1. 定义 `CommandResult` 枚举：`CONTINUE` / `EXIT`
2. 定义 `handle_command(text, session) -> tuple[CommandResult, str]`：`/exit` → EXIT + 告别语；`/clear` → session.clear() + 清空提示；`/help` → 命令列表说明；未知 `/xxx` → CONTINUE + 提示未知命令
3. 导出 `COMMAND_NAMES = ["/exit", "/clear", "/help"]`（供补全用）
**验证：** `python -c` 依次调用三命令断言返回值与副作用（session 被清空）

## T12: TUI 主循环

**文件：** `mewcode/tui/app.py`
**依赖：** T11、T6、T4
**步骤：**
1. 实现 `run(provider, config)`：
   - 创建 `PromptSession`：`NestedCompleter.from_nested_dict({cmd: None for cmd in COMMAND_NAMES})`、默认内存历史（上下键回溯）
   - 打印欢迎语（Rich 样式，含当前配置 `name` 与 `model`；绝不打印 `api_key`）
   - 主循环：读输入 → 空输入跳过；`/` 开头走 `handle_command`（EXIT 则退出循环，反馈文本用 Rich 打印）→ 否则进入对话轮次
2. 对话轮次实现：
   - `session.add_user(text)` 并打印用户消息（带角色前缀样式）
   - `provider.stream(session.messages)` 迭代：ThinkingDelta 以灰色缩进实时打印（首次进入思考时先打印「💭 思考中…」区头）；TextDelta 追加到本轮 buffer，用 `Live(Markdown(buffer))` 逐字刷新；Done 后组装 `Message`（text + thinking 拼接 + thinking_raw）调 `session.add_assistant`
   - `ProviderError` → Rich 红色 Panel 显示错误信息；回滚本轮 user 消息；回到输入
3. prompt_toolkit 与 Rich 输出共存：用 `patch_stdout` 处理输出区域，保证输入行始终在最底部
**验证：** 用 T10 的 mock 服务写一次性脚本（或临时配置指向 mock）：启动 TUI，程序化输入一轮对话，观察流式事件顺序渲染无异常、无崩溃；`/exit` 正常退出

## T13: CLI 入口

**文件：** `mewcode/cli.py`、`mewcode/__main__.py`
**依赖：** T12、T2、T9
**步骤：**
1. `cli.py`：argparse 定义 `--profile`（缺省 None）与 `--config`（缺省 `~/.mewcode/config.yaml`）；`main()` 依次：load_config → select_profile → create_provider → tui.run；捕获 `ConfigError` 打印到 stderr 并 `sys.exit(1)`
2. `__main__.py`：`from mewcode.cli import main; main()`
**验证：** ① 配置文件缺失 → 退出码非零、stderr 有中文错误（AC10 基础）② 正常配置 + mock → 进入 TUI ③ `--profile` 选择第二份配置时欢迎语显示对应 name

## T14: 示例配置

**文件：** `config.example.yaml`
**依赖：** T2
**步骤：** 写示例：`providers:` 列表含 anthropic 与 openai 各一份，`api_key` 用 `sk-xxxx` 占位并注释说明获取方式；thinking 一份 true 一份 false 展示可选性
**验证：** `python -c` 用 `load_config` 加载示例文件，断言两份配置字段正确

## 执行顺序

```
T1 → T2 → T3
T1 → T4 → T5
T4 → T6 → T7 ─┐
         → T8 ─┴→ T9 → T10
T4 → T11 → T12 → T13
T2 → T14（可与 T3–T13 并行）
```
