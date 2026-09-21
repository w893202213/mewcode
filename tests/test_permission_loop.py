"""权限系统 Agent Loop 集成测试：ask 挂起/应答、五种拒绝路径不终止。"""

import asyncio
from pathlib import Path

import pytest

from mewcode.agent.events import (
    PermissionAskEvent,
    RoundEnd,
    RoundEndReason,
    ToolCallStarted,
    ToolResultEvent,
)
from mewcode.agent.loop import Agent
from mewcode.permissions import Action, Mode, PermissionGate, Rule, RuleSet
from mewcode.provider.base import (
    Done,
    ProviderError,
    TextDelta as PTextDelta,
    ToolCall,
)
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0

    def stream(self, messages, tools, system):
        self.calls += 1
        return iter(self._handler(self.calls, messages, tools))


def _collect(agent: Agent, text: str, on_ask=None) -> list:
    """在独立事件循环里跑完一轮，返回全部事件；on_ask 在收到询问时同步应答。"""

    async def go():
        events = []
        async for event in agent.run(text):
            if isinstance(event, PermissionAskEvent) and on_ask is not None:
                on_ask(event)
            events.append(event)
        return events

    return asyncio.run(go())


def _gate(tmp_path, *rules: Rule, mode: Mode = Mode.DEFAULT) -> PermissionGate:
    rs = RuleSet()
    for r in rules:
        rs.add(r)
    return PermissionGate(tmp_path.resolve(), rs, mode)


def _end(events: list) -> RoundEnd:
    return events[-1]


def _results(events: list) -> list[ToolResultEvent]:
    return [e for e in events if isinstance(e, ToolResultEvent)]


# ---------------------------------------------------------------------------
# ask 挂起/应答（T9）
# ---------------------------------------------------------------------------


def test_ask_event_then_allow_executes(tmp_path):
    """ASK 先发 PermissionAskEvent；应答放行后执行，循环正常结束。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="run_command", args={"command": "echo hi"})]
        return [PTextDelta("执行完成"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))
    events = _collect(agent, "hi", on_ask=lambda e: agent.answer_permission(True))

    asks = [e for e in events if isinstance(e, PermissionAskEvent)]
    assert len(asks) == 1
    assert "默认模式" in asks[0].reason
    # 事件顺序：ToolCallStarted → PermissionAskEvent → 结果
    kinds = [type(e) for e in events]
    assert kinds.index(ToolCallStarted) < kinds.index(PermissionAskEvent)
    assert kinds.index(PermissionAskEvent) < kinds.index(ToolResultEvent)
    assert _results(events)[0].result.ok
    assert _end(events).reason is RoundEndReason.OK


def test_ask_deny_feeds_back_and_loop_continues(tmp_path):
    """拒绝后回灌失败结果、不终止循环，模型可调整策略。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="run_command", args={"command": "echo hi"})]
        return [PTextDelta("好的，我换个方式"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))
    events = _collect(agent, "hi", on_ask=lambda e: agent.answer_permission(False))

    assert len([e for e in events if isinstance(e, PermissionAskEvent)]) == 1
    result = _results(events)[0]
    assert not result.result.ok
    assert "拒绝" in result.result.content
    assert _end(events).reason is RoundEndReason.OK
    assert "换个方式" in _end_text(events)


def test_ask_rule_reason(tmp_path):
    """ask 规则命中的询问原因含规则信息。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="run_command", args={"command": "git push"})]
        return [PTextDelta("收到"), Done()]

    gate = _gate(tmp_path, Rule("run_command", "git *", Action.ASK))
    agent = Agent(FakeProvider(handler), create_registry(), session, gate=gate)
    events = _collect(agent, "hi", on_ask=lambda e: agent.answer_permission(False))
    asks = [e for e in events if isinstance(e, PermissionAskEvent)]
    assert len(asks) == 1
    assert "询问规则" in asks[0].reason


def test_allow_rule_no_ask(tmp_path):
    """命中 allow 规则：不询问直接执行。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="run_command", args={"command": "git status"})]
        return [PTextDelta("收到"), Done()]

    gate = _gate(tmp_path, Rule("run_command", "git *", Action.ALLOW))
    agent = Agent(FakeProvider(handler), create_registry(), session, gate=gate)
    events = _collect(agent, "hi")
    assert not [e for e in events if isinstance(e, PermissionAskEvent)]
    assert _results(events)[0].result.ok


def test_cancel_releases_pending_ask(tmp_path):
    """询问挂起时取消：pending 被解除，以 CANCELLED 收尾不悬挂。"""
    session = ChatSession()

    def handler(n, messages, tools):
        return [ToolCall(id="c1", name="run_command", args={"command": "echo hi"})]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))
    events = _collect(agent, "hi", on_ask=lambda e: agent.cancel())
    assert _end(events).reason is RoundEndReason.CANCELLED


def test_answer_after_cancel_is_noop(tmp_path):
    """取消后再应答（竞态）：静默忽略，不影响收尾。"""
    session = ChatSession()

    def handler(n, messages, tools):
        return [ToolCall(id="c1", name="run_command", args={"command": "echo hi"})]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))
    events = _collect(
        agent, "hi",
        on_ask=lambda e: (agent.cancel(), agent.answer_permission(True)),
    )
    assert _end(events).reason is RoundEndReason.CANCELLED


def test_readonly_tools_no_ask(tmp_path):
    """只读工具默认不询问，直接执行。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="read_file", args={"file_path": "a.py"})]
        return [PTextDelta("收到"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))
    events = _collect(agent, "hi")
    assert not [e for e in events if isinstance(e, PermissionAskEvent)]


# ---------------------------------------------------------------------------
# 五种拒绝路径：均不终止循环（AC7）
# ---------------------------------------------------------------------------


def _deny_path_test(tmp_path, call, gate, expect_reason_fragment):
    """通用拒绝路径：首轮被拒 → 模型下一轮给文本 → RoundEnd(OK)。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [call]
        return [PTextDelta("已按你的约束调整"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=gate)
    events = _collect(agent, "hi")
    results = _results(events)
    assert len(results) == 1
    assert not results[0].result.ok
    assert expect_reason_fragment in results[0].result.content
    assert _end(events).reason is RoundEndReason.OK


def test_deny_blacklist_loop_continues(tmp_path):
    gate = _gate(tmp_path, mode=Mode.ACCEPT)
    _deny_path_test(
        tmp_path,
        ToolCall(id="c1", name="run_command", args={"command": "sudo rm -rf /"}),
        gate,
        "黑名单",
    )


def test_deny_sandbox_loop_continues(tmp_path):
    gate = _gate(tmp_path, mode=Mode.ACCEPT)
    _deny_path_test(
        tmp_path,
        ToolCall(id="c1", name="write_file", args={"file_path": "../x.txt", "content": "x"}),
        gate,
        "越界",
    )


def test_deny_rule_loop_continues(tmp_path):
    gate = _gate(tmp_path, Rule("run_command", "git *", Action.DENY))
    _deny_path_test(
        tmp_path,
        ToolCall(id="c1", name="run_command", args={"command": "git push"}),
        gate,
        "拒绝规则",
    )


def test_deny_strict_mode_loop_continues(tmp_path):
    gate = _gate(tmp_path, mode=Mode.STRICT)
    _deny_path_test(
        tmp_path,
        ToolCall(id="c1", name="run_command", args={"command": "git status"}),
        gate,
        "严格模式",
    )


def test_deny_user_reject_loop_continues(tmp_path):
    gate = _gate(tmp_path)
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="run_command", args={"command": "echo hi"})]
        return [PTextDelta("已按你的约束调整"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=gate)
    events = _collect(agent, "hi", on_ask=lambda e: agent.answer_permission(False))
    results = _results(events)
    assert not results[0].result.ok
    assert "拒绝" in results[0].result.content
    assert _end(events).reason is RoundEndReason.OK


def _end_text(events: list) -> str:
    """拼接事件流里的全部文本增量。"""
    return "".join(e.text for e in events if isinstance(e, PTextDelta))
