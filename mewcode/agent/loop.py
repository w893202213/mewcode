"""Agent Loop 主体：ReAct 循环、分批执行、停止条件、Plan Mode。

Agent 只产出事件（agent/events.py），完全不感知界面。provider 的同步
流经 _stream_bridged 的泵线程桥入 asyncio 世界——协议层零改动；
取消 = 停止消费，泵线程（daemon）自然结束。
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

from mewcode.agent.collector import StreamCollector
from mewcode.agent.events import (
    ProgressEvent,
    RoundEnd,
    RoundEndReason,
    ToolCallStarted,
    ToolResultEvent,
    UsageEvent,
)
from mewcode.agent.executor import execute_call
from mewcode.provider.base import (
    Done,
    Provider,
    ProviderError,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
    ToolCall,
)
from mewcode.session import ChatSession, ToolResultRecord, ToolUse
from mewcode.tools.base import Tool, ToolError, ToolResult
from mewcode.tools.registry import ToolRegistry

# 迭代上限：兜底安全网（spec N1）
MAX_ITERATIONS = 30
# 连续调用未知工具达到该次数即停止（spec F2④）
UNKNOWN_TOOL_LIMIT = 2

# Plan Mode 切换时注入会话的提示（以 user 消息形式，两家协议通用）
PLAN_MODE_ON_MESSAGE = "（Plan Mode 已开启：只能使用只读工具调研，请给出计划，不要修改文件或执行命令。）"
PLAN_MODE_OFF_MESSAGE = "（Plan Mode 已关闭：已切回执行模式，可以按计划动手了。）"

# 泵线程 → 消费者的一次等待轮询间隔（秒）：取消检查的最长延迟
_CANCEL_POLL_INTERVAL = 0.05


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
    aq: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """泵线程：跑同步 provider.stream()，事件跨线程灌入 asyncio 队列。"""
    try:
        for event in provider.stream(messages, tools):
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
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._session = session
        self._max_iterations = max_iterations
        self._plan_mode = False
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """线程安全：请求停止循环（进行中轮次丢弃，已落盘轮次保留）。"""
        self._cancelled.set()

    def enter_plan_mode(self) -> None:
        """进入计划模式：置标志并注入提示 user 消息（幂等）。"""
        if not self._plan_mode:
            self._plan_mode = True
            self._session.add_user(PLAN_MODE_ON_MESSAGE)

    def exit_plan_mode(self) -> None:
        """退出计划模式：清标志并注入提示 user 消息（幂等）。"""
        if self._plan_mode:
            self._plan_mode = False
            self._session.add_user(PLAN_MODE_OFF_MESSAGE)

    @property
    def plan_mode(self) -> bool:
        """当前是否处于计划模式。"""
        return self._plan_mode

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
        self, messages: list, tools: list[Tool]
    ) -> AsyncIterator[StreamEvent]:
        """把同步 provider 流转成可取消的异步流（泵线程 + call_soon_threadsafe）。

        取消通过 wait_for 轮询察觉：即使流在等待下一片，也最多
        _CANCEL_POLL_INTERVAL 秒后抛 _Cancelled。
        """
        aq: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        thread = threading.Thread(
            target=_pump_provider,
            args=(self._provider, messages, tools, aq, loop),
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
        start_index = len(self._session.messages)
        self._session.add_user(user_text)
        total_in = 0
        total_out = 0
        unknown_streak = 0  # 连续未知工具计数（本轮内跨迭代累计）
        try:
            for iteration in range(1, self._max_iterations + 1):
                yield ProgressEvent(iteration)
                if self._cancelled_now():
                    raise _Cancelled()
                collector = StreamCollector()
                calls: list[ToolCall] = []
                async for event in self._stream_bridged(
                    self._session.messages, self._visible_tools()
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
                yield UsageEvent(input_tokens=total_in, output_tokens=total_out)
                if not calls:
                    # 停止条件①：模型不再请求工具，正常结束
                    yield RoundEnd(RoundEndReason.OK)
                    return
                # 分批执行（F5）：只读工具并发、副作用工具按序串行
                # 分组基于调用在模型响应中的顺序（dataclass 相等判断对重复调用不可靠）
                readonly_flags = [self._is_read_only(c) for c in calls]
                readonly = [c for c, f in zip(calls, readonly_flags) if f]
                side_effect = [c for c, f in zip(calls, readonly_flags) if not f]
                for call in readonly:
                    yield ToolCallStarted(call)
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
                    yield ToolCallStarted(call)
                    side_results.append(
                        await asyncio.to_thread(execute_call, self._registry, call)
                    )
                    if self._cancelled_now():
                        raise _Cancelled()
                # 执行完、回灌前再查取消：取消则丢弃本轮结果，不写会话
                if self._cancelled_now():
                    raise _Cancelled()
                # 按模型原始调用顺序统一回灌 + 发结果事件（N7）
                results: dict[int, ToolResult] = {}
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
