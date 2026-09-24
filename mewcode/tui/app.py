"""TUI 主循环：输入、Agent 事件消费渲染、错误显示。

组合方式：prompt_toolkit PromptSession 管输入（历史/补全），Rich 管输出
（Markdown 渲染、Live 流式刷新、思考块灰色展示）。Agent 在后台线程跑
事件循环（run_in_background），事件经线程安全队列交给主线程渲染；
patch_stdout 让 Rich 与输入行共存，输入行始终停在最底部。
"""

from __future__ import annotations

import json
import queue
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.shortcuts import prompt
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from mewcode import __version__
from mewcode.agent import Agent, run_in_background
from mewcode.agent.events import (
    CompressEvent,
    PermissionAskEvent,
    ProgressEvent,
    RoundEnd,
    RoundEndReason,
    ThinkingDelta,
    TextDelta,
    ToolCallStarted,
    ToolResultEvent,
    UsageEvent,
)
from mewcode.config import ProviderConfig
from mewcode.hooks.manager import HookManager
from mewcode.memory import (
    PROJECT_MEMORY_DIR,
    USER_MEMORY_DIR,
    MemoryContext,
    NoteManager,
    NoteUpdater,
)
from mewcode.commands.builtins import build_registry
from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import parse_command
from mewcode.commands.registry import CommandRegistry
from mewcode.permissions import MODE_LABELS, Action, Mode, PermissionGate, RuleSet, exact_rule
from mewcode.provider import create_provider
from mewcode.provider.base import Provider
from mewcode.session import ChatSession
from mewcode.skills.manager import SkillManager
from mewcode.tools import ToolRegistry
from mewcode.tui.completion import CommandCompleter
from mewcode.tui.double_press import DoublePressTracker, ExitSignal
from mewcode.tui.uicontroller import TuiUiController

# 渲染样式常量
_ASSISTANT_PREFIX = "[bold magenta]🐱 MewCode[/bold magenta]"
# 计时行样式（用户要求灰色，区别于正文）
_TIMER_STYLE = "grey50"
# Ctrl+C 双击退出提示（显示在输入行下方 bottom_toolbar）
_EXIT_HINT = "再按一次 Ctrl+C 退出"
# 工具调用展示截断：参数摘要 200 字符、结果面板 2000 字符（显示截断，回灌内容不受影响）
_ARGS_SUMMARY_CHARS = 200
_DISPLAY_RESULT_CHARS = 2000
# 取消后等待 RoundEnd 的最大秒数（防御：后台异常时界面不悬挂）
_CANCEL_DRAIN_TIMEOUT = 5.0


def _print_welcome(
    console: Console,
    config: ProviderConfig,
    gate: PermissionGate,
    resume_summary: str = "",
) -> None:
    """打印欢迎语（含配置名与模型，绝不回显 api_key）；ch11：恢复信息行。"""
    lines = [
        f"配置: [bold]{config.name}[/bold]（协议 {config.protocol}）",
        f"模型: {config.model}",
        f"思考模式: {'开启' if config.thinking else '关闭'}",
        f"权限模式: {MODE_LABELS[gate.mode]}（{gate.mode.value}，/mode 切换）",
    ]
    if resume_summary:
        lines.append(resume_summary)
    lines.append("输入 /help 查看可用命令。")
    console.print(
        Panel("\n".join(lines), title=f"[bold]MewCode v{__version__}[/bold]", border_style="cyan")
    )


def run(
    provider: Provider,
    config: ProviderConfig,
    registry: ToolRegistry,
    custom_instructions: str = "",
    gate: PermissionGate | None = None,
    memory: MemoryContext | None = None,
    command_registry: CommandRegistry | None = None,
    skill_manager: SkillManager | None = None,
    skills_summary: str = "",
    hook_manager: HookManager | None = None,
) -> None:
    """TUI 主循环：读输入 → 分流（命令走本地分发，其余送 Agent）→ 回到输入。

    memory（ch11）：记忆上下文——会话存档、索引加载器、笔记更新器；
    为 None 时按纯内存会话运行（测试/嵌入场景，行为与 ch10 一致）。
    command_registry（ch12）：命令注册中心，None 时用内置命令构建
    （cli 启动期已构建校验，冲突会在此抛异常）。
    skill_manager / skills_summary（ch13）：技能管理器（激活状态、白名单、
    技能命令注册）与技能清单（注入系统提示 stable）；None 时按无技能运行。
    hook_manager（ch15）：Hook 管理器，None 时按无 Hook 运行；会话级事件
    （session_start/session_end）在此触发，独立技能路径同。
    """
    if command_registry is None:
        command_registry = build_registry()
    if gate is None:
        # 缺省闸门（与 Agent 缺省一致）：项目根=cwd、默认档、空规则
        gate = PermissionGate(Path.cwd().resolve(), RuleSet(), Mode.DEFAULT)
    root = Path.cwd().resolve()
    session = memory.session if memory is not None else ChatSession()
    # ch13 F7：技能命令注册到命令中心（激活时经管理器登记；撞内置名跳过）
    if skill_manager is not None:
        skill_manager.attach_registry(command_registry)
    agent = Agent(
        provider,
        registry,
        session,
        custom_instructions=custom_instructions,
        gate=gate,
        context_window=config.context_window,  # ch09 F1
        memory_loader=memory.memory_loader if memory is not None else None,
        skills_summary=skills_summary,
        skill_manager=skill_manager,
        hook_manager=hook_manager,  # ch15：主 Agent 接入 Hook
    )
    # ch11 F5：轮自然结束后异步更新记忆；提示经队列送回主线程打印
    notice_queue: queue.Queue[str] = queue.Queue()
    updater: NoteUpdater | None = None
    if memory is not None:
        updater = NoteUpdater(
            provider,
            project=NoteManager(root / PROJECT_MEMORY_DIR),
            user=NoteManager(Path.home() / ".mewcode" / USER_MEMORY_DIR),
            notice=notice_queue.put,
        )

    # 双击 Ctrl+C 判定：第一次提示（不清行），3 秒内第二次退出
    tracker = DoublePressTracker()
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        """Ctrl+C：覆盖默认 abort（KeyboardInterrupt），改为双击退出。"""
        if tracker.press():
            event.app.exit(result=ExitSignal.EXIT)
        else:
            event.app.invalidate()  # 立即重绘，让提示出现

    # 默认 InMemoryHistory：上下键回溯本次会话输入
    # bottom_toolbar：状态栏（权限档 + [PLAN]），Ctrl+C 双击提示激活时临时替换
    # refresh_interval：0.5s 重绘一次，提示 3 秒后自动过期消失
    prompt_session = PromptSession(
        message=[("class:prompt", "❯ ")],
        completer=CommandCompleter(command_registry),
        key_bindings=kb,
        bottom_toolbar=lambda: _status_toolbar(tracker, gate, agent),
        refresh_interval=0.5,
    )

    def _invalidate() -> None:
        """状态栏立即重绘：仅当输入会话活跃时有效，否则下轮 prompt 天然带新状态。"""
        app = getattr(prompt_session, "app", None)
        if app is not None:
            app.invalidate()

    # patch_stdout(raw=True)：Rich 输出与 prompt_toolkit 输入行共存
    with patch_stdout(raw=True):
        console = Console()

        def _run_agent_round(text: str) -> None:
            """提示词类命令注入用户消息并触发完整 Agent 一轮（闭包晚绑定 ui）。"""
            _agent_round(
                console,
                agent,
                text,
                tracker,
                gate,
                session,
                memory.log if memory is not None else None,
                updater,
                ui,
            )

        def _run_independent_skill(name: str, args: str) -> None:
            """独立模式技能执行（ch13 F6/F8）：独立对话跑完，摘要回流 + 自动续一轮。

            独立对话复用主循环事件渲染（流式可见、权限弹窗），但不落会话
            存档（log=None）；摘要 = 独立对话最终助手回复，回流主历史后
            自动触发主对话一轮让模型继续或汇报。
            """
            if skill_manager is None:
                console.print("技能系统未启用。")
                return
            result = skill_manager.activate(name, args)  # 热更新 + 激活状态
            if not result.ok:
                console.print(f"技能执行失败: {result.error}")
                return
            skill = result.skill
            # 独立会话：新鲜会话 + 主对话最近 history 条历史（F6）
            inner_session = ChatSession()
            if skill.history > 0:
                inner_session.restore(session.messages[-skill.history:])
            # 独立管理器：共享技能字典、激活状态隔离（预激活本技能）
            inner_manager = SkillManager(skill_manager.skills)
            inner_activated = inner_manager.activate(name, args)
            if not inner_activated.ok:
                console.print(f"技能执行失败: {inner_activated.error}")
                return
            # model 字段：派生 ProviderConfig 重建 provider（仅独立模式生效）
            inner_provider = provider
            if skill.model:
                inner_provider = create_provider(replace(config, model=skill.model))
            inner_agent = Agent(
                inner_provider,
                registry,
                inner_session,
                custom_instructions=custom_instructions,
                gate=gate,
                context_window=config.context_window,
                skills_summary=skills_summary,
                skill_manager=inner_manager,
                hook_manager=hook_manager,
                # 独立存盘子目录：避免与外层 ResultStore 序号撞车
                context_dir=root / ".mewcode" / "context" / "skills",
            )
            # ch15：独立会话同样触发会话级事件（会话/轮次/消息/工具级生效）
            if hook_manager is not None:
                hook_manager.emit("session_start", {"event": "session_start"})
            start_index = len(inner_session.messages)
            task_text = args or f"请执行技能 {name}"
            try:
                end = _agent_round(
                    console,
                    inner_agent,
                    task_text,
                    tracker,
                    gate,
                    inner_session,
                    log=None,
                    updater=None,
                    ui=ui,
                    prefix=f"[bold magenta]🐱 MewCode[/bold magenta]（技能 {name} 独立执行）",
                )
            finally:
                if hook_manager is not None:
                    hook_manager.emit("session_end", {"event": "session_end"})
            # 摘要 = 独立对话新增消息中最后一条助手回复；失败回流失败说明
            summary = None
            for m in reversed(inner_session.messages[start_index:]):
                if m.role == "assistant" and m.text:
                    summary = m.text
                    break
            if summary is None:
                summary = f"独立执行未完成: {end.message if end is not None else '已取消'}"
            console.print(
                Panel(
                    summary,
                    title=f"[bold]技能 {name} 独立执行摘要[/bold]",
                    border_style="cyan",
                )
            )
            # 摘要回流主历史 + 自动触发主对话一轮（F8，已确认的自动续轮）
            _run_agent_round(f"【技能 {name} 独立执行摘要】\n{summary}")

        def _drain_pending_independent() -> None:
            """轮末取走并执行模型登记的独立技能（use_skill 工具路径，F8）。"""
            if skill_manager is None:
                return
            pending = skill_manager.take_pending()
            if pending is not None:
                _run_independent_skill(*pending)

        # ch12 F4：命令处理函数与渲染框架之间的唯一桥梁
        ui = TuiUiController(
            console=console,
            session=session,
            agent=agent,
            gate=gate,
            memory=memory,
            config=config,
            root=root,
            run_agent_round=_run_agent_round,
            invalidate=_invalidate,
            skill_manager=skill_manager,
            run_independent=_run_independent_skill,
        )
        _print_welcome(console, config, gate, memory.resume_summary if memory else "")

        # ch15：会话级事件——主循环前后配对触发，覆盖全部退出路径
        if hook_manager is not None:
            hook_manager.emit("session_start", {"event": "session_start"})
        try:
            while True:
                try:
                    text = prompt_session.prompt()
                except KeyboardInterrupt:
                    continue  # 非按键来源的 SIGINT 安全网；按键 Ctrl+C 已被自定义绑定接管
                except EOFError:
                    console.print("再见！👋")
                    return
                if text is ExitSignal.EXIT:
                    return  # 双击 Ctrl+C：正常退出，回到 CLI
                # ch11：先打印上轮异步笔记更新的提示（若有），再处理本次输入
                _drain_notices(console, notice_queue)
                # ch12 F5 回车入口分流器：斜杠命令走本地分发，其余送 Agent
                parsed = parse_command(text)
                if parsed is None:
                    if not text.strip():
                        continue  # 空白输入早返回
                    _run_agent_round(text)
                    _drain_pending_independent()  # ch13 F8：本轮结束后执行登记的独立技能
                    continue
                if dispatch(command_registry, parsed, ui) is CommandResult.EXIT:
                    return
        finally:
            if hook_manager is not None:
                hook_manager.emit("session_end", {"event": "session_end"})


def _agent_round(
    console: Console,
    agent: Agent,
    text: str,
    tracker: DoublePressTracker,
    gate: PermissionGate,
    session: ChatSession,
    log: SessionLog | None,
    updater: NoteUpdater | None,
    ui: TuiUiController,
    prefix: str = _ASSISTANT_PREFIX,
) -> RoundEnd:
    """一轮 Agent Loop：后台跑 Agent，主线程消费事件渲染（F9）。

    用户消息不回显：prompt_toolkit 输入行已留在终端滚动区，问题天然可见。
    Ctrl+C 取消循环（已完成步骤保留）；取消后继续排空队列直到 RoundEnd。
    结束后（ch11 F2/F5）：本轮新增消息追加落盘 JSONL；自然结束（OK）
    时触发异步笔记更新（busy 时内部跳过，不阻塞输入）。
    结束后（ch12）：token 用量记录进 ui（/status 数据源）。
    prefix（ch13）：助手回复前缀可定制（独立执行显示技能名）；
    返回 RoundEnd（独立执行判断成败用，主循环调用方忽略）。
    """
    start_index = len(session.messages)
    q = run_in_background(agent, text)
    # 助手回复前缀独立成行，事件流内容渲染在它下方
    console.print(prefix)
    usage: dict[str, int | None] = {"in": 0, "out": 0, "cache_hit": None, "cache_write": None}
    end: RoundEnd | None = None
    try:
        end = _consume_events(console, q, usage, agent, gate)
    except KeyboardInterrupt:
        agent.cancel()
        tracker.reset()  # 双击退出计数从头开始
        try:
            end = _drain_cancelled(console, q, usage)
        except KeyboardInterrupt:
            end = None  # 用户再按 Ctrl+C：不再等待后台收尾
    if end is None:
        end = RoundEnd(RoundEndReason.CANCELLED, "已取消")
    _print_end(console, usage, end)
    # ch11：落盘本轮新增消息（ERROR 已被 Agent 回滚，此处只含完整轮次）
    if log is not None:
        new_messages = session.messages[start_index:]
        if new_messages:
            log.append_messages(new_messages, time.time())
        if end.reason is RoundEndReason.OK and updater is not None:
            updater.submit(session.messages)
    # ch12：记录最近一轮与累计 token（/status 数据源）
    ui.record_usage(usage)
    return end


def _drain_notices(console: Console, notice_queue: queue.Queue) -> None:
    """打印积压的异步提示（记忆更新等）：主线程在 prompt 返回后调用，无并发问题。"""
    while True:
        try:
            notice = notice_queue.get_nowait()
        except queue.Empty:
            return
        console.print(Text(notice, style=_TIMER_STYLE))


def _status_toolbar(tracker: DoublePressTracker, gate: PermissionGate, agent: Agent) -> str | None:
    """状态栏：权限档英文标记 + 计划模式 [PLAN]（ch12 F4）。

    每次重绘直读 live 状态（0.5s 定时兜底），模式切换后立即反映；
    Ctrl+C 双击提示激活时临时替换显示。返回纯字符串：bottom_toolbar
    只接受 prompt_toolkit 格式化文本（rich Text 会直接抛异常）。
    """
    if tracker.hint_active():
        return _EXIT_HINT
    parts = [f"[{gate.mode.value.upper()}]"]
    if agent.plan_mode:
        parts.append("[PLAN]")
    return " ".join(parts)


def _consume_events(
    console: Console,
    q: queue.Queue,
    usage: dict[str, int | None],
    agent: Agent,
    gate: PermissionGate,
) -> RoundEnd:
    """消费事件队列直到 RoundEnd；返回结束事件。

    每轮一个 Live（流式正文 + 思考计时），工具调用与结果在流结束后逐条
    打印（ch04 样式）。帧高有上限（_max_live_lines）：帧高超出终端高度时
    Rich 会把整帧滚进历史区（同一内容重复出现），因此「第 N 轮」行在轮初
    一次性打印、已完结的 markdown 块一次性提交到滚动区，帧内只保留思考
    行与当前未完结块。KeyboardInterrupt 向上传播给 _agent_round 处理取消；
    finally 保证 Live 一定关闭。
    """
    live: Live | None = None
    indicator: ThinkingIndicator | None = None
    iteration = 0
    thinking = ""
    reply: list[str] = []
    tail = ""  # 帧内显示的当前未完结块（源文本）
    committed_blocks = 0  # 已一次性提交（打印）的 markdown 块数
    committed_tail_lines = 0  # 当前块已按行提交过的开头行数（超高截断用）
    max_lines = _max_live_lines(console)

    def render() -> None:
        if live is not None:
            # 思考文本同样受行数上限约束，避免长思考撑爆帧高
            shown_thinking = "\n".join(thinking.splitlines()[-max_lines:])
            live.update(_stream_renderable(shown_thinking, tail, indicator))

    def close_live() -> None:
        nonlocal live
        if live is not None:
            live.stop()
            live = None

    try:
        while True:
            try:
                event = q.get(timeout=0.1)
            except queue.Empty:
                continue
            if isinstance(event, ProgressEvent):
                # 新一轮：关闭上一轮 Live（工具面板已打印），打印进度行，开新 Live
                close_live()
                iteration = event.iteration
                thinking = ""
                reply.clear()
                tail = ""
                committed_blocks = 0
                committed_tail_lines = 0
                indicator = ThinkingIndicator()
                console.print(Text(f"第 {iteration} 轮", style=_TIMER_STYLE))
                live = Live(
                    indicator,
                    console=console,
                    refresh_per_second=10,
                    vertical_overflow="visible",
                )
                live.start()
                render()
            elif isinstance(event, ThinkingDelta):
                thinking += event.text
                render()
            elif isinstance(event, TextDelta):
                reply.append(event.text)
                if live is None:
                    continue
                blocks = _split_markdown_blocks("".join(reply))
                if not blocks:
                    continue
                committed_text = ""
                if len(blocks) - 1 > committed_blocks:
                    # 已完结的块一次性提交到滚动区（不再重绘），帧内只保留当前块；
                    # 若上一个块已按行提交过开头（超高截断），只补交其余部分
                    pieces: list[str] = []
                    for i, block in enumerate(blocks[committed_blocks:-1]):
                        lines = block.splitlines()
                        if i == 0 and committed_tail_lines:
                            lines = lines[committed_tail_lines:]
                        pieces.append("\n".join(lines))
                    committed_text = "\n\n".join(pieces) + "\n\n"
                    committed_blocks = len(blocks) - 1
                    committed_tail_lines = 0
                tail = blocks[-1]
                tail_lines = tail.splitlines()
                overflow = max(len(tail_lines) - max_lines, 0)
                if overflow > committed_tail_lines:
                    # 单个块超高（如大段代码围栏）：新增的超出行先提交，帧内保留末尾 max_lines 行
                    committed_text += "\n".join(
                        tail_lines[committed_tail_lines:overflow]
                    )
                    committed_tail_lines = overflow
                tail = "\n".join(tail_lines[overflow:])
                if overflow and tail_lines:
                    # 围栏块被截断后，保留的尾部补一个围栏开头，否则中段会退化成段落
                    opener = tail_lines[0].strip()
                    if opener.startswith("```") or opener.startswith("~~~"):
                        tail = tail_lines[0] + "\n" + tail
                if committed_text:
                    render()
                    # 先同步帧内容再打印提交文本，提交块才会落在旧帧上方而非下方
                    live.refresh()
                    console.print(Markdown(committed_text))
                else:
                    render()
            elif isinstance(event, CompressEvent):
                # ch09 F7：压缩提示灰色行；轮初压缩时（summarize_*/breaker）重开
                # Live 承接后续流式渲染，prevent 阶段（live 已关）只打印
                was_live = live is not None
                close_live()
                console.print(Text(event.message, style=_TIMER_STYLE))
                if was_live:
                    live = Live(
                        indicator,
                        console=console,
                        refresh_per_second=10,
                        vertical_overflow="visible",
                    )
                    live.start()
                    render()
            elif isinstance(event, ToolCallStarted):
                close_live()
                _print_tool_call(console, event.call)
            elif isinstance(event, PermissionAskEvent):
                close_live()
                # 人在回路：主线程询问用户，应答后放行/拒绝（ch07 F5）
                allow = _ask_user(console, event, gate)
                agent.answer_permission(allow)
            elif isinstance(event, ToolResultEvent):
                _print_tool_result(console, event.result)
            elif isinstance(event, UsageEvent):
                usage["in"] = event.input_tokens
                usage["out"] = event.output_tokens
                usage["cache_hit"] = event.cache_hit_tokens
                usage["cache_write"] = event.cache_write_tokens
            elif isinstance(event, RoundEnd):
                if live is not None:
                    if event.reason is RoundEndReason.ERROR:
                        # 清空半截正文：错误面板上方不残留流式内容
                        live.update(Text(""), refresh=True)
                    live.stop()
                    live = None
                return event
    finally:
        close_live()


def _drain_cancelled(
    console: Console, q: queue.Queue, usage: dict[str, int | None]
) -> RoundEnd | None:
    """取消后继续消费队列直到 RoundEnd（渲染途中到达的工具面板），超时放弃。"""
    deadline = time.monotonic() + _CANCEL_DRAIN_TIMEOUT
    while time.monotonic() < deadline:
        try:
            event = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if isinstance(event, ToolCallStarted):
            _print_tool_call(console, event.call)
        elif isinstance(event, ToolResultEvent):
            _print_tool_result(console, event.result)
        elif isinstance(event, CompressEvent):
            console.print(Text(event.message, style=_TIMER_STYLE))
        elif isinstance(event, UsageEvent):
            usage["in"] = event.input_tokens
            usage["out"] = event.output_tokens
            usage["cache_hit"] = event.cache_hit_tokens
            usage["cache_write"] = event.cache_write_tokens
        elif isinstance(event, RoundEnd):
            return event
    return None


def _print_end(console: Console, usage: dict[str, int | None], end: RoundEnd) -> None:
    """轮次收尾：token 用量行（含缓存命中/写入，服务端返回才展示）+ 停止原因面板。"""
    parts: list[str] = []
    if usage.get("in") or usage.get("out"):
        parts.append(f"输入 {usage['in']} · 输出 {usage['out']}")
    if usage.get("cache_hit") is not None:
        parts.append(f"缓存命中 {usage['cache_hit']}")
    if usage.get("cache_write") is not None:
        parts.append(f"缓存写入 {usage['cache_write']}")
    if parts:
        console.print(Text("本轮 token: " + " · ".join(parts), style=_TIMER_STYLE))
    if end.reason is RoundEndReason.ERROR:
        console.print(
            Panel(end.message or "请求失败", title="[bold red]请求失败[/bold red]", border_style="red")
        )
    elif end.reason is RoundEndReason.CANCELLED:
        console.print(
            Panel(
                "已取消（已完成步骤保留）。",
                title="[bold yellow]已取消[/bold yellow]",
                border_style="yellow",
            )
        )
    elif end.reason is RoundEndReason.LIMIT:
        console.print(
            Panel(end.message, title="[bold yellow]已达迭代上限[/bold yellow]", border_style="yellow")
        )
    elif end.reason is RoundEndReason.UNKNOWN_TOOL:
        console.print(
            Panel(
                "连续调用未知工具，已停止。",
                title="[bold yellow]已停止[/bold yellow]",
                border_style="yellow",
            )
        )


def _ask_user(console: Console, event: PermissionAskEvent, gate: PermissionGate) -> bool:
    """权限询问：展示面板并等待用户选择；返回是否放行（ch07 F5）。

    y=本次允许 / s=本会话允许（登记会话规则）/ a=永久允许（写本地级 YAML）
    / n=拒绝。Ctrl+C 向上抛（沿既有取消路径）；EOF 按拒绝处理（循环继续）。
    """
    summary = json.dumps(event.call.args, ensure_ascii=False)
    if len(summary) > _ARGS_SUMMARY_CHARS:
        summary = summary[: _ARGS_SUMMARY_CHARS] + "…"
    console.print(
        Panel(
            f"工具: {event.call.name}\n参数: {summary}\n原因: {event.reason}",
            title="[bold yellow]⚠ 需要权限确认[/bold yellow]",
            border_style="yellow",
        )
    )
    while True:
        try:
            answer = prompt("  [y] 本次允许  [s] 本会话允许  [a] 永久允许  [n] 拒绝 > ")
        except EOFError:
            return False  # EOF 按拒绝：循环继续，模型可调整策略
        answer = answer.strip().lower()
        if answer in ("y", "s", "a", "n"):
            break
    if answer == "y":
        return True
    if answer == "n":
        return False
    # 会话/永久允许：按最小授权登记精确规则（ch07 技术决策）
    rule = exact_rule(event.call.name, event.call.args, Action.ALLOW)
    if answer == "s":
        gate.grant_session(rule)
    else:
        gate.grant_permanent(rule)
    return True


def _print_tool_call(console: Console, call) -> None:
    """展示工具调用：工具名 + 参数摘要（F9）。"""
    args_summary = json.dumps(call.args, ensure_ascii=False)
    if len(args_summary) > _ARGS_SUMMARY_CHARS:
        args_summary = args_summary[: _ARGS_SUMMARY_CHARS] + "…"
    console.print(Text(f"🔧 {call.name} {args_summary}", style="dim"))


def _print_tool_result(console: Console, result) -> None:
    """展示工具结果面板（成功绿/失败红，按显示上限截断）。"""
    display = result.content
    if len(display) > _DISPLAY_RESULT_CHARS:
        display = display[: _DISPLAY_RESULT_CHARS] + f"\n…（终端显示截断，共 {len(result.content)} 字符）"
    if result.ok:
        console.print(
            Panel(display, title="[bold green]工具结果[/bold green]", border_style="green")
        )
    else:
        console.print(
            Panel(display, title="[bold red]工具失败[/bold red]", border_style="red")
        )


class ThinkingIndicator:
    """时间感知渲染对象：每次渲染时用单调时钟计算已思考秒数。

    rich Live 的 auto_refresh 线程会周期性重渲染当前 renderable，
    因此本对象无需额外线程即可让秒数自动跳动；clock 可注入便于测试。
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._start = clock()

    def __rich_console__(self, console, options):
        """渲染灰色 thinking… Ns 计时行（N 为经过的整秒数）。"""
        elapsed = int(self._clock() - self._start)
        yield Text(f"thinking… {elapsed}s", style=_TIMER_STYLE)


def _max_live_lines(console: Console) -> int:
    """Live 重绘区的行数上限：终端高度留 2 行余量（至少 8 行）。

    帧高超过终端高度时，Rich 的原地重绘会退化成整帧滚动，
    每一帧都完整落进历史区，造成同一内容反复出现。
    """
    return max(8, (console.height or 24) - 2)


def _split_markdown_blocks(text: str) -> list[str]:
    """按空行把流式正文切成 markdown 块；代码围栏内的空行不切（围栏块整体提交）。"""
    blocks: list[str] = []
    cur: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        if not stripped and not in_fence:
            if cur:
                blocks.append("\n".join(cur))
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append("\n".join(cur))
    return blocks


def _stream_renderable(
    thinking: str, reply: str, indicator: ThinkingIndicator
) -> Group:
    """组装 Live 渲染内容：思考期灰色计时（有思考内容时并在下方）→ 流式正文（当前未完结块）。

    「第 N 轮」进度行不在帧内：由 _consume_events 在轮初一次性打印，
    避免帧随提交下移时把进度行带进正文中间。
    """
    parts: list = []
    if reply:
        parts.append(Markdown(reply))
    else:
        parts.append(indicator)
        if thinking:
            parts.append(Text(thinking, style="dim"))
    return Group(*parts)
