"""Agent Loop 主体：ReAct 循环、分批执行、停止条件、Plan Mode。

Agent 只产出事件（agent/events.py），完全不感知界面。provider 的同步
流经 _stream_bridged 的泵线程桥入 asyncio 世界——协议层零改动；
取消 = 停止消费，泵线程（daemon）自然结束。
"""

from __future__ import annotations

import asyncio
import os
import platform
import threading
from collections.abc import AsyncIterator
from pathlib import Path

from mewcode.agent.collector import StreamCollector
from mewcode.agent.events import (
    CompressEvent,
    PermissionAskEvent,
    ProgressEvent,
    RoundEnd,
    RoundEndReason,
    ToolCallStarted,
    ToolResultEvent,
    UsageEvent,
)
from mewcode.agent.executor import execute_call
from mewcode.context import (
    Compressor,
    ResultStore,
    StoreError,
    SummarizeError,
    TokenEstimator,
)
from mewcode.provider.base import (
    Done,
    Provider,
    ProviderError,
    StreamEvent,
    SystemPrompt,
    TextDelta,
    ThinkingDelta,
    ToolCall,
)
from mewcode.permissions import Action, Mode, PermissionGate, RuleSet
from mewcode.prompt import (
    PLAN_MODE_DIRECTIVE,
    Environment,
    SwitchDirective,
    SystemPromptBuilder,
)
from mewcode.session import ChatSession, ToolResultRecord, ToolUse
from mewcode.tools.base import Tool, ToolError, ToolResult
from mewcode.tools.registry import ToolRegistry

# 迭代上限：兜底安全网（spec N1）
MAX_ITERATIONS = 30
# 连续调用未知工具达到该次数即停止（spec F2④）
UNKNOWN_TOOL_LIMIT = 2

# 泵线程 → 消费者的一次等待轮询间隔（秒）：取消检查的最长延迟
_CANCEL_POLL_INTERVAL = 0.05


def _safe_cwd() -> str:
    """启动目录采样：极端情况失败时用占位符，不让补充块拖垮启动。"""
    try:
        return os.getcwd()
    except OSError:
        return "未知"


class _FailingStore:
    """存盘目录不可用时的占位 store：save 恒抛 StoreError，prevent 恒降级（N6）。"""

    def save(self, content: str, seq: int) -> None:
        raise StoreError("存盘目录不可用")

    def preview(self, content: str, path, head_chars: int) -> str:
        return content  # save 先抛，此处不会被调到


class _Cancelled(Exception):
    """内部哨兵：用户取消，由 run() 捕获转成 RoundEnd(CANCELLED)。"""


class _StreamError(Exception):
    """泵线程捕获的流异常包装，消费者重新抛出。"""

    def __init__(self, error: BaseException) -> None:
        self.error = error


class _StreamEnd:
    """流结束哨兵（单例）。"""


_STREAM_END = _StreamEnd()


def _push(aq: asyncio.Queue, loop: asyncio.AbstractEventLoop, item) -> bool:
    """跨线程投递到 asyncio 队列；事件循环已关闭（消费者已放弃）时返回 False。"""
    try:
        loop.call_soon_threadsafe(aq.put_nowait, item)
        return True
    except RuntimeError:
        return False


def _pump_provider(
    provider: Provider,
    messages: list,
    tools: list[Tool],
    system: SystemPrompt,
    aq: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """泵线程：跑同步 provider.stream()，事件跨线程灌入 asyncio 队列。"""
    try:
        for event in provider.stream(messages, tools, system):
            if not _push(aq, loop, event):
                return  # 消费者已放弃（如取消后事件循环关闭）
    except Exception as e:  # 含 ProviderError：包装后交给消费者抛出
        _push(aq, loop, _StreamError(e))
    finally:
        _push(aq, loop, _STREAM_END)


class Agent:
    """ReAct 循环主体：一轮用户输入 → 多轮「思考→工具→结果」→ RoundEnd。"""

    def __init__(
        self,
        provider: Provider,
        registry: ToolRegistry,
        session: ChatSession,
        max_iterations: int = MAX_ITERATIONS,
        custom_instructions: str = "",
        gate: PermissionGate | None = None,
        context_window: int = 200000,  # 上下文窗口上限（ch09 F1）
        context_dir: Path | None = None,  # 工具结果存盘目录（ch09 F3，缺省启动目录/.mewcode/context）
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._session = session
        self._max_iterations = max_iterations
        self._plan_mode = False
        self._cancelled = threading.Event()
        # 权限闸门：缺省建默认闸门（项目根=cwd、默认档、空规则）；
        # 测试与 TUI 显式传入带规则/档位的闸门
        self._gate = (
            gate
            if gate is not None
            else PermissionGate(Path.cwd().resolve(), RuleSet(), Mode.DEFAULT)
        )
        # 挂起的权限询问 (future, 事件循环)；无询问时为 None
        self._pending: tuple[asyncio.Future, asyncio.AbstractEventLoop] | None = None
        # 系统提示拼装器：环境快照（启动目录+平台）构造时采样，日期每次 build 现取；
        # 自定义指令来自配置（可选）
        self._builder = SystemPromptBuilder(
            Environment(cwd=_safe_cwd(), platform=platform.platform()),
            custom_instructions=custom_instructions,
        )
        # ch09：上下文管理——估算器、存盘器、压缩器；存盘目录不可用时降级
        # 为恒失败 store，prevent 静默降级、不阻断请求（N6）
        self._estimator = TokenEstimator()
        try:
            store = ResultStore(
                context_dir or Path.cwd() / ".mewcode" / "context"
            )
        except StoreError:
            store = _FailingStore()
        self._compressor = Compressor(
            provider, self._estimator, store, context_window
        )

    def cancel(self) -> None:
        """线程安全：请求停止循环；同时解除挂起的权限询问（置拒绝）。

        进行中轮次丢弃，已落盘轮次保留。
        """
        self._cancelled.set()
        pending = self._pending
        if pending is not None:
            fut, loop = pending
            try:
                loop.call_soon_threadsafe(fut.set_result, False)
            except RuntimeError:
                pass  # 事件循环已关闭：等待自然结束

    @property
    def permission_mode(self) -> Mode:
        """当前权限档位（ch07 F4）。"""
        return self._gate.mode

    def cycle_permission_mode(self) -> Mode:
        """循环切换权限档位并返回新档位（strict→default→accept→strict）。"""
        return self._gate.cycle_mode()

    def answer_permission(self, allow: bool) -> None:
        """线程安全：回答挂起的权限询问（TUI 主线程调用）。

        无挂起询问时静默忽略（取消/结束后的竞态应答）。
        """
        pending = self._pending
        if pending is None:
            return
        fut, loop = pending
        try:
            loop.call_soon_threadsafe(fut.set_result, allow)
        except RuntimeError:
            pass  # 事件循环已关闭：应答无意义

    async def _await_answer(self, fut: asyncio.Future) -> bool:
        """等待应答 future；期间轮询取消标志（取消即抛 _Cancelled）。

        调用方须在发出 PermissionAskEvent 前把 (fut, loop) 登记到
        self._pending——TUI 收到事件即可立刻应答，无竞态窗口。
        """
        try:
            while True:
                done, _ = await asyncio.wait({fut}, timeout=_CANCEL_POLL_INTERVAL)
                if self._cancelled_now():
                    raise _Cancelled()
                if done:
                    return bool(fut.result())
        finally:
            self._pending = None

    def enter_plan_mode(self) -> None:
        """进入计划模式：只置标志（幂等）；指令由每轮系统补充块注入，不写会话。"""
        self._plan_mode = True

    def exit_plan_mode(self) -> None:
        """退出计划模式：只清标志（幂等）。"""
        self._plan_mode = False

    @property
    def plan_mode(self) -> bool:
        """当前是否处于计划模式。"""
        return self._plan_mode

    def compact_now(self) -> str:
        """/compact 同步入口（ch09 F5）：无条件执行重量兜底，返回中文反馈。

        无视熔断（F6：手动总是允许，成功即解除）。TUI 主线程直接调用——
        命令只会在输入提示时处理，此时 Agent 循环必然已结束，无并发。
        """
        system = self._builder.build([], 1)
        try:
            result = self._compressor.summarize(self._session, manual=True)
        except SummarizeError as e:
            return f"压缩失败: {e}"
        if result.compacted_messages == 0:
            return "上下文较短，无需压缩。"
        return (
            f"已压缩上下文：{result.compacted_messages} 条消息生成摘要，"
            f"{result.kept_user_messages} 条用户消息原文保留，"
            f"估算节省 {result.est_saved_tokens} token。"
        )

    def _visible_tools(self) -> list[Tool]:
        """本轮发给模型的工具列表：计划模式下收窄为只读工具。"""
        tools = self._registry.list()
        if self._plan_mode:
            return [t for t in tools if t.read_only]
        return tools

    def _is_read_only(self, call: ToolCall) -> bool:
        """只读判定（分批并发分组用）；未知工具按副作用串行处理（反正会失败）。"""
        try:
            return self._registry.get(call.name).read_only
        except ToolError:
            return False

    def _is_unknown(self, call: ToolCall) -> bool:
        """未知工具判定（F2④ 停止条件用）：注册表查不到名字。"""
        try:
            self._registry.get(call.name)
            return False
        except ToolError:
            return True

    def _cancelled_now(self) -> bool:
        return self._cancelled.is_set()

    async def _stream_bridged(
        self, messages: list, tools: list[Tool], system: SystemPrompt
    ) -> AsyncIterator[StreamEvent]:
        """把同步 provider 流转成可取消的异步流（泵线程 + call_soon_threadsafe）。

        取消通过 wait_for 轮询察觉：即使流在等待下一片，也最多
        _CANCEL_POLL_INTERVAL 秒后抛 _Cancelled。
        """
        aq: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=_pump_provider,
            args=(self._provider, messages, tools, system, aq, loop),
            daemon=True,
            name="provider-pump",
        )
        thread.start()
        while True:
            try:
                item = await asyncio.wait_for(aq.get(), timeout=_CANCEL_POLL_INTERVAL)
            except asyncio.TimeoutError:
                if self._cancelled_now():
                    raise _Cancelled()
                continue
            if item is _STREAM_END:
                return
            if isinstance(item, _StreamError):
                raise item.error
            yield item

    async def run(self, user_text: str) -> AsyncIterator:
        """执行一轮 Agent Loop，逐个产出事件，永远以 RoundEnd 收尾（N2）。

        结构：加用户消息 → 逐轮「流式收集 → 落盘 → 执行工具回灌」，
        直到模型不再调工具或触发停止条件。异常路径回滚本轮（start_index 起）。
        """
        self._cancelled.clear()
        self._pending = None
        start_index = len(self._session.messages)
        self._session.add_user(user_text)
        total_in = 0
        total_out = 0
        total_cache_hit: int | None = None  # 本轮累计缓存命中（服务端不返回则 None）
        total_cache_write: int | None = None  # 本轮累计缓存写入
        unknown_streak = 0  # 连续未知工具计数（本轮内跨迭代累计）
        try:
            for iteration in range(1, self._max_iterations + 1):
                yield ProgressEvent(iteration)
                if self._cancelled_now():
                    raise _Cancelled()
                # 每轮组装系统提示：stable 逐字节不变（缓存命中），
                # dynamic 随开关指令与轮次变化（注入节奏见 SwitchDirective）
                directives: list[SwitchDirective] = (
                    [PLAN_MODE_DIRECTIVE] if self._plan_mode else []
                )
                system = self._builder.build(directives, iteration)
                # ch09 F4/F6：请求前检查是否逼近窗口上限——触发重量兜底；
                # 熔断时提示但不压缩，按未压缩状态继续（N6）
                if self._compressor.needs_auto(self._session.messages, system):
                    yield CompressEvent(
                        kind="summarize_start",
                        message="正在压缩上下文：较早的消息将生成摘要，近期原文保留…",
                    )
                    try:
                        result = await asyncio.to_thread(
                            self._compressor.summarize, self._session, False
                        )
                        yield CompressEvent(
                            kind="summarize_done",
                            message=(
                                f"已压缩上下文：{result.compacted_messages} 条消息生成摘要，"
                                f"{result.kept_user_messages} 条用户消息原文保留，"
                                f"估算节省 {result.est_saved_tokens} token"
                            ),
                            compacted_messages=result.compacted_messages,
                            est_saved_tokens=result.est_saved_tokens,
                        )
                    except SummarizeError as e:
                        yield CompressEvent(
                            kind="summarize_failed", message=f"压缩失败: {e}"
                        )
                        if self._compressor.breaker_open:
                            # 第 3 次失败：立即告知熔断（可能没有下一轮请求前的检查机会）
                            yield CompressEvent(
                                kind="breaker",
                                message=(
                                    "摘要已连续失败 3 次（熔断），自动压缩不再发起；"
                                    "可输入 /compact 手动重试"
                                ),
                            )
                elif self._compressor.breaker_open and self._compressor.over_auto_threshold(
                    self._session.messages, system
                ):
                    yield CompressEvent(
                        kind="breaker",
                        message=(
                            "上下文接近上限，但摘要已连续失败 3 次（熔断），"
                            "自动压缩不再发起；可输入 /compact 手动重试"
                        ),
                    )
                collector = StreamCollector()
                calls: list[ToolCall] = []
                async for event in self._stream_bridged(
                    self._session.messages, self._visible_tools(), system
                ):
                    if self._cancelled_now():
                        raise _Cancelled()
                    if isinstance(event, TextDelta):
                        collector.add_text(event.text)
                        yield event
                    elif isinstance(event, ThinkingDelta):
                        collector.add_thinking(event.text)
                        yield event
                    elif isinstance(event, ToolCall):
                        calls.append(event)
                    elif isinstance(event, Done):
                        collector.set_thinking_raw(event.thinking_raw)
                        if event.usage:
                            total_in += event.usage.input_tokens
                            total_out += event.usage.output_tokens
                            if event.usage.cache_hit_tokens is not None:
                                total_cache_hit = (
                                    (total_cache_hit or 0) + event.usage.cache_hit_tokens
                                )
                            if event.usage.cache_write_tokens is not None:
                                total_cache_write = (
                                    (total_cache_write or 0) + event.usage.cache_write_tokens
                                )
                            # ch09 F2：锚定最新 usage——后续估算基于真实值 + 增量差分
                            self._estimator.anchor(
                                event.usage.input_tokens, self._session.messages
                            )
                # 流结束后立刻检查：取消时不落盘本轮部分响应
                if self._cancelled_now():
                    raise _Cancelled()
                self._session.add_assistant(
                    text=collector.full_text,
                    thinking=collector.thinking_text,
                    thinking_raw=collector.thinking_raw,
                    tool_uses=[
                        ToolUse(id=c.id, name=c.name, args=c.args) for c in calls
                    ],
                )
                yield UsageEvent(
                    input_tokens=total_in,
                    output_tokens=total_out,
                    cache_hit_tokens=total_cache_hit,
                    cache_write_tokens=total_cache_write,
                )
                if not calls:
                    # 停止条件①：模型不再请求工具，正常结束
                    yield RoundEnd(RoundEndReason.OK)
                    return
                # 执行阶段（ch05 F5 + ch07 F7）：逐调用预检直通——
                # 计划模式过滤沿用 → 闸门裁决；ALLOW 进执行队列、
                # DENY 立即失败结果、ASK 挂起等应答；事件与回灌按模型
                # 原始调用顺序（分组基于顺序：dataclass 相等判断对重复调用不可靠）
                results: dict[int, ToolResult] = {}
                planned: list[ToolCall] = []
                for call in calls:
                    yield ToolCallStarted(call)
                    if self._plan_mode and not self._is_read_only(call):
                        # 计划模式只放开读类工具：副作用工具不执行，回灌失败结果（F7）
                        results[id(call)] = ToolResult.failure(
                            "计划模式已禁用该工具（仅只读工具可用，输入 /do 切回执行模式）"
                        )
                        continue
                    try:
                        tool = self._registry.get(call.name)
                    except ToolError:
                        planned.append(call)  # 未知工具无闸门可查，交给 execute_call 报错
                        continue
                    verdict = self._gate.check(tool, call)
                    if verdict.action is Action.DENY:
                        results[id(call)] = ToolResult.failure(verdict.reason)
                        continue
                    if verdict.action is Action.ASK:
                        # 先登记 pending 再发事件：TUI 收到事件即可立刻应答
                        loop = asyncio.get_running_loop()
                        fut: asyncio.Future = loop.create_future()
                        self._pending = (fut, loop)
                        yield PermissionAskEvent(call, verdict.reason)
                        if await self._await_answer(fut):
                            planned.append(call)
                        else:
                            results[id(call)] = ToolResult.failure(
                                "用户拒绝了该工具的权限请求"
                            )
                        continue
                    planned.append(call)
                    if self._cancelled_now():
                        raise _Cancelled()
                # 执行通过预检的调用：只读并发、副作用串行（ch05 F5）
                readonly_flags = [self._is_read_only(c) for c in planned]
                readonly = [c for c, f in zip(planned, readonly_flags) if f]
                side_effect = [c for c, f in zip(planned, readonly_flags) if not f]
                ro_results = await asyncio.gather(
                    *(
                        asyncio.to_thread(execute_call, self._registry, c)
                        for c in readonly
                    )
                )
                if self._cancelled_now():
                    raise _Cancelled()
                side_results: list = []
                for call in side_effect:
                    side_results.append(
                        await asyncio.to_thread(execute_call, self._registry, call)
                    )
                    if self._cancelled_now():
                        raise _Cancelled()
                # 执行完、回灌前再查取消：取消则丢弃本轮结果，不写会话
                if self._cancelled_now():
                    raise _Cancelled()
                # 按模型原始调用顺序统一回灌 + 发结果事件（N7）
                for call, result in zip(readonly, ro_results):
                    results[id(call)] = result
                for call, result in zip(side_effect, side_results):
                    results[id(call)] = result
                for call in calls:
                    result = results[id(call)]
                    self._session.add_tool_result(
                        ToolResultRecord(
                            tool_use_id=call.id,
                            content=result.content,
                            is_error=not result.ok,
                        )
                    )
                    # ch09 F3：轻量预防——超阈值结果存盘，会话只留预览与路径
                    prevent_event = self._compressor.prevent(
                        self._session.messages[-1]
                    )
                    if prevent_event is not None:
                        yield prevent_event
                    yield ToolResultEvent(call, result)
                    # 停止条件④：连续调用未知工具（任何已知工具清零计数）
                    if self._is_unknown(call):
                        unknown_streak += 1
                        if unknown_streak >= UNKNOWN_TOOL_LIMIT:
                            yield RoundEnd(
                                RoundEndReason.UNKNOWN_TOOL,
                                "连续调用未知工具，已停止",
                            )
                            return
                    else:
                        unknown_streak = 0
            # 停止条件②：循环走完仍未结束（兜底安全网）
            yield RoundEnd(
                RoundEndReason.LIMIT, f"已达迭代上限（{self._max_iterations} 轮）"
            )
        except _Cancelled:
            # 停止条件③：用户取消（已落盘轮次保留，进行中轮次不落盘）
            yield RoundEnd(RoundEndReason.CANCELLED, "已取消")
        except ProviderError as e:
            del self._session.messages[start_index:]
            yield RoundEnd(RoundEndReason.ERROR, str(e))
        except Exception as e:
            # 兜底：任何意外异常都以 RoundEnd 收尾，界面不会挂起
            del self._session.messages[start_index:]
            yield RoundEnd(RoundEndReason.ERROR, f"内部错误: {e}")
