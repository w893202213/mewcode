"""Agent Loop 核心测试：FakeProvider + 真实工具，直接消费事件流（N6）。"""

import asyncio
import time

import pytest

from mewcode.agent.events import (
    ProgressEvent,
    RoundEnd,
    RoundEndReason,
    TextDelta,
    ToolCallStarted,
    ToolResultEvent,
    UsageEvent,
)
from mewcode.agent.loop import Agent
from mewcode.provider.base import Done, ProviderError, TextDelta as PTextDelta, ThinkingDelta, ToolCall, UsageInfo
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。

    system 单独记录在 systems 里，供断言每轮注入的系统提示内容。
    """

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.systems = []

    def stream(self, messages, tools, system):
        self.calls += 1
        self.systems.append(system)
        return iter(self._handler(self.calls, messages, tools))


def _collect(agent: Agent, text: str) -> list:
    """在独立事件循环里跑完一轮，返回全部事件。"""

    async def go():
        return [event async for event in agent.run(text)]

    return asyncio.run(go())


def _text_events(events: list) -> str:
    """拼接事件流里的全部文本增量（界面看到的内容）。"""
    return "".join(e.text for e in events if isinstance(e, TextDelta))


def test_single_round_no_tools_ok():
    """停止条件①：模型不调工具，正常结束；事件序列完整。"""
    session = ChatSession()

    def handler(n, messages, tools):
        return [PTextDelta("你好"), Done()]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "问好")
    assert [type(e).__name__ for e in events] == ["ProgressEvent", "TextDelta", "UsageEvent", "RoundEnd"]
    assert _text_events(events) == "你好"
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.OK
    # 会话：user + assistant 完整落盘（AC4）
    assert session.messages[0].text == "问好"
    assert session.messages[1].text == "你好"


def test_multi_round_progress_to_ok(tmp_path):
    """多轮推进（AC1）：第一轮读文件，第二轮给出最终回复。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                PTextDelta("我先看看文件"),
                ToolCall(id="toolu_1", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]
        return [PTextDelta("文件里 x 是 1，任务完成"), Done()]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "看看文件")
    kinds = [type(e).__name__ for e in events]
    assert kinds == [
        "ProgressEvent", "TextDelta", "UsageEvent",
        "ToolCallStarted", "ToolResultEvent",
        "ProgressEvent", "TextDelta", "UsageEvent", "RoundEnd",
    ]
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.OK
    # 中间轮：assistant 文本 + 工具调用落盘；工具结果回灌（F1）
    mid = session.messages[1]
    assert mid.text == "我先看看文件"
    assert [u.name for u in mid.tool_uses] == ["read_file"]
    assert session.messages[2].tool_results[0].tool_use_id == "toolu_1"
    # 最终回复落盘
    assert session.messages[3].text == "文件里 x 是 1，任务完成"


def test_second_round_request_contains_tool_results(tmp_path):
    """下一轮请求的 messages 包含上一轮工具调用与结果（AC1）。"""
    target = tmp_path / "b.py"
    target.write_text("y = 2\n", encoding="utf-8")
    session = ChatSession()
    seen: list[list] = []

    def handler(n, messages, tools):
        seen.append(list(messages))
        if n == 1:
            return [
                ToolCall(id="toolu_1", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]
        return [PTextDelta("好了"), Done()]

    _collect(Agent(FakeProvider(handler), create_registry(), session), "读文件")
    # 第二轮请求里：assistant 消息带 tool_use，user 消息带 tool_result
    second = seen[1]
    assert any(m.role == "assistant" and m.tool_uses for m in second)
    assert any(m.role == "user" and m.tool_results for m in second)


def test_usage_accumulates_across_rounds():
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="t1", name="grep_code", args={"pattern": "x"}), Done(usage=UsageInfo(10, 5))]
        return [PTextDelta("完"), Done(usage=UsageInfo(20, 8))]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "查")
    usages = [e for e in events if isinstance(e, UsageEvent)]
    assert [(u.input_tokens, u.output_tokens) for u in usages] == [(10, 5), (30, 13)]
    # 服务端不返回缓存字段：事件里为 None（优雅降级）
    assert usages[0].cache_hit_tokens is None and usages[0].cache_write_tokens is None


def test_cache_usage_accumulates_across_rounds():
    """缓存命中/写入随轮次累计进 UsageEvent（F6）。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="grep_code", args={"pattern": "x"}),
                Done(usage=UsageInfo(10, 5, cache_hit_tokens=0, cache_write_tokens=90)),
            ]
        return [PTextDelta("完"), Done(usage=UsageInfo(20, 8, cache_hit_tokens=90))]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "查")
    usages = [e for e in events if isinstance(e, UsageEvent)]
    assert usages[0].cache_hit_tokens == 0
    assert usages[0].cache_write_tokens == 90
    assert usages[1].cache_hit_tokens == 90
    assert usages[1].cache_write_tokens == 90  # 第二轮无写入，累计值不变


def test_system_prompt_sent_every_round():
    """每轮请求都携带系统提示：stable 含七模块、dynamic 含环境信息（F1/F3）。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="t1", name="grep_code", args={"pattern": "x"}), Done()]
        return [PTextDelta("完"), Done()]

    provider = FakeProvider(handler)
    _collect(Agent(provider, create_registry(), session), "查")
    assert len(provider.systems) == 2
    # 两轮 stable 逐字节一致（缓存前缀不变）
    assert provider.systems[0].stable == provider.systems[1].stable
    assert "## 身份" in provider.systems[0].stable
    assert "## 文本输出" in provider.systems[0].stable
    # dynamic：环境信息补充块（工作目录/操作系统/当前日期）
    assert "<supplement>" in provider.systems[0].dynamic
    assert "【环境信息】" in provider.systems[0].dynamic
    assert "工作目录" in provider.systems[0].dynamic
    assert "当前日期" in provider.systems[0].dynamic


def test_provider_error_rolls_back_round():
    """停止条件⑤：流出错 → 回滚本轮全部消息，RoundEnd(ERROR)。"""

    def handler(n, messages, tools):
        raise ProviderError("服务端返回错误: mock 过载")

    session = ChatSession()
    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "触发错误")
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.ERROR
    assert "mock 过载" in end.message
    assert session.messages == []  # 回滚：用户消息不留在上下文


def test_unexpected_exception_rolls_back_with_internal_error():
    def handler(n, messages, tools):
        raise RuntimeError("boom")

    session = ChatSession()
    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "触发异常")
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.ERROR
    assert "内部错误" in end.message and "boom" in end.message
    assert session.messages == []


def test_thinking_forwarded_and_collected():
    """思考增量：转发给事件流的同时落盘会话。"""
    session = ChatSession()

    def handler(n, messages, tools):
        return [
            ThinkingDelta("让我想想"),
            PTextDelta("答案是42"),
            Done(thinking_raw=[{"type": "thinking", "thinking": "让我想想", "signature": "sig"}]),
        ]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "问")
    thinkings = [e for e in events if isinstance(e, ThinkingDelta)]
    assert [t.text for t in thinkings] == ["让我想想"]
    assert session.messages[1].thinking == "让我想想"
    assert session.messages[1].thinking_raw == [{"type": "thinking", "thinking": "让我想想", "signature": "sig"}]


# ---------------------------------------------------------------------------
# T10: 分批执行与停止条件 ②③④
# ---------------------------------------------------------------------------

import threading

from mewcode.tools.base import Tool, ToolResult
from mewcode.tools.registry import ToolRegistry


class _TimedTool(Tool):
    """记录执行起止时间与线程 ID 的测试工具（验证并发/串行）。"""

    def __init__(self, name: str, seconds: float, read_only: bool, log: list):
        self.name = name
        self.description = f"测试工具 {name}"
        self.input_schema = {"type": "object", "properties": {}}
        self.read_only = read_only
        self._seconds = seconds
        self._log = log

    def execute(self, args: dict) -> ToolResult:
        start = time.monotonic()
        self._log.append(("start", self.name, start, threading.get_ident()))
        time.sleep(self._seconds)
        end = time.monotonic()
        self._log.append(("end", self.name, end, threading.get_ident()))
        return ToolResult.success(f"{self.name} 完成")


def _interval(log: list, name: str) -> tuple[float, float]:
    """取某工具的执行区间（start 时间, end 时间）。"""
    start = next(e for e in log if e[0] == "start" and e[1] == name)
    end = next(e for e in log if e[0] == "end" and e[1] == name)
    return start[2], end[2]


def test_batch_concurrent_readonly_serial_side_effect():
    """F5：单响应含三个工具调用——两个只读并发、副作用串行且在其后；回灌按原始顺序。"""
    log: list[tuple] = []
    registry = ToolRegistry()
    read_a = _TimedTool("read_a", 0.2, True, log)
    read_b = _TimedTool("read_b", 0.2, True, log)
    write_tool = _TimedTool("write_tool", 0.1, False, log)
    for t in (read_a, write_tool, read_b):
        registry.register(t)
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            # 原始顺序：read_a → write_tool → read_b
            return [
                ToolCall(id="t1", name="read_a", args={}),
                ToolCall(id="t2", name="write_tool", args={}),
                ToolCall(id="t3", name="read_b", args={}),
                Done(),
            ]
        return [PTextDelta("完成"), Done()]

    events = _collect(Agent(FakeProvider(handler), registry, session), "批量执行")
    # 结果事件顺序 = 模型原始调用顺序（N7）
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert [e.call.id for e in results] == ["t1", "t2", "t3"]
    # 两个只读工具并发：执行区间重叠且线程不同
    a_start, a_end = _interval(log, "read_a")
    b_start, b_end = _interval(log, "read_b")
    assert a_start < b_end and b_start < a_end  # 区间重叠
    a_thread = next(e[3] for e in log if e[0] == "start" and e[1] == "read_a")
    b_thread = next(e[3] for e in log if e[0] == "start" and e[1] == "read_b")
    assert a_thread != b_thread
    # 副作用工具在全部只读完成后才执行（串行）
    w_start, _ = _interval(log, "write_tool")
    assert w_start >= a_end and w_start >= b_end
    # 会话回灌顺序 = 原始顺序（N7，每条结果一条消息）
    assert [m.tool_results[0].tool_use_id for m in session.messages[2:5]] == [
        "t1",
        "t2",
        "t3",
    ]
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.OK


def test_consecutive_unknown_tools_stop():
    """停止条件④：连续两轮调用未知工具后停止（AC2）。"""

    def handler(n, messages, tools):
        return [ToolCall(id=f"u{n}", name="no_such_tool", args={}), Done()]

    session = ChatSession()
    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "触发未知")
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.UNKNOWN_TOOL
    assert "未知工具" in end.message
    # 两轮都被落盘：user + 2×(assistant + 结果)
    assert len(session.messages) == 5
    assert len([e for e in events if isinstance(e, ProgressEvent)]) == 2


def test_known_tool_resets_unknown_streak(tmp_path):
    """未知 → 已知 → 未知 → 未知：已知工具调用清零计数，第 4 轮才停止。"""
    target = tmp_path / "d.py"
    target.write_text("z = 3\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 2:
            return [
                ToolCall(id=f"k{n}", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]
        return [ToolCall(id=f"u{n}", name="no_such_tool", args={}), Done()]

    events = _collect(Agent(FakeProvider(handler), create_registry(), session), "混合调用")
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.UNKNOWN_TOOL
    assert len([e for e in events if isinstance(e, ProgressEvent)]) == 4


def test_iteration_limit_reached(tmp_path):
    """停止条件②：每轮都有工具调用，达到迭代上限后停止（AC2）。"""
    target = tmp_path / "e.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        return [
            ToolCall(id=f"t{n}", name="read_file", args={"file_path": str(target)}),
            Done(),
        ]

    agent = Agent(FakeProvider(handler), create_registry(), session, max_iterations=3)
    events = _collect(agent, "循环不停")
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.LIMIT
    assert "上限" in end.message
    assert len([e for e in events if isinstance(e, ProgressEvent)]) == 3
    # 三轮全部落盘：user + 3×(assistant + 结果)
    assert len(session.messages) == 7


def test_cancel_keeps_completed_rounds_discards_inflight(tmp_path):
    """停止条件③：取消后已落盘轮次保留、进行中轮次丢弃（AC2）。"""
    target = tmp_path / "f.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()
    release = threading.Event()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]

        # 第二轮：吐一段文本后挂起，模拟长流；等待测试方取消
        def slow():
            yield PTextDelta("慢慢说")
            release.wait(timeout=5)
            yield Done()

        return slow()

    agent = Agent(FakeProvider(handler), create_registry(), session)

    async def go():
        events = []
        async for e in agent.run("开始"):
            events.append(e)
            # 在第二轮流中途取消：先转发过「慢慢说」再取消
            if isinstance(e, TextDelta) and e.text == "慢慢说":
                agent.cancel()
                release.set()  # 放行泵线程的挂起流
        return events

    events = asyncio.run(go())
    end = events[-1]
    assert isinstance(end, RoundEnd) and end.reason is RoundEndReason.CANCELLED
    # 第一轮已落盘保留：user + 第一轮 assistant + 第一轮结果
    assert len(session.messages) == 3
    assert session.messages[1].tool_uses[0].id == "t1"
    # 第二轮进行中：文本已转发但未落盘
    assert "慢慢说" in _text_events(events)
    assert len(session.messages) == 3  # 第二轮 assistant 未写入


# ---------------------------------------------------------------------------
# T11: 线程桥
# ---------------------------------------------------------------------------

from mewcode.agent.bridge import run_in_background


def test_run_in_background_delivers_all_events_ending_with_round_end():
    """线程桥：事件完整经队列交付，以 RoundEnd 收尾。"""
    session = ChatSession()

    def handler(n, messages, tools):
        return [PTextDelta("桥接你好"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session)
    q = run_in_background(agent, "测试桥")
    events = []
    while True:
        event = q.get(timeout=5)
        events.append(event)
        if isinstance(event, RoundEnd):
            break
    assert _text_events(events) == "桥接你好"
    assert events[-1].reason is RoundEndReason.OK
    assert session.messages[1].text == "桥接你好"
