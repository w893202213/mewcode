"""子 Agent 模式与主循环的接入测试（ch16 F5/F6/F7）：非交互 ASK 自动拒绝、
工具集过滤、读缓存实例隔离。"""

import asyncio
from pathlib import Path

from mewcode.agent.events import PermissionAskEvent, RoundEndReason, ToolResultEvent
from mewcode.agent.loop import Agent
from mewcode.agents.context import ParentContext, clear, push
from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.provider.base import Done, TextDelta as PTextDelta, ToolCall
from mewcode.session import ChatSession
from mewcode.tools import create_registry
from mewcode.tools.files import FileReadCache, ReadFileTool


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.systems = []

    def stream(self, messages, tools, system):
        self.calls += 1
        self.systems.append(system)
        return iter(self._handler(self.calls, messages, tools))


def _collect(agent: Agent, text: str) -> list:
    async def go():
        return [event async for event in agent.run(text)]

    return asyncio.run(go())


def _gate(tmp_path: Path) -> PermissionGate:
    return PermissionGate(tmp_path.resolve(), RuleSet(), Mode.DEFAULT)


# ----------------------------------------------------------------------
# 非交互模式：ASK 自动拒绝（F6）
# ----------------------------------------------------------------------


def test_non_interactive_ask_auto_denied(tmp_path):
    mark = tmp_path / "executed"
    session = ChatSession()

    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="run_command", args={"command": f"touch {mark}"}),
                Done(),
            ]
        if n == 2:
            # 模型收到拒绝原因后改用只读工具继续
            return [
                ToolCall(id="t2", name="read_file", args={"file_path": str(tmp_path / "x.py")}),
                Done(),
            ]
        return [PTextDelta("完成"), Done()]

    agent = Agent(
        FakeProvider(handler),
        create_registry(),
        session,
        gate=_gate(tmp_path),
        interactive=False,
    )
    events = _collect(agent, "执行任务")
    # 无权限询问事件（非交互不挂起）
    assert not [e for e in events if isinstance(e, PermissionAskEvent)]
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 2
    assert results[0].result.ok is False
    assert "非交互模式无法人工确认" in results[0].result.content
    assert not mark.exists()  # 副作用工具未执行
    assert events[-1].reason is RoundEndReason.OK


def test_interactive_ask_still_asks(tmp_path):
    """interactive=True（缺省）行为不变：发询问事件、可应答放行。"""
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="run_command", args={"command": "echo hi"}),
                Done(),
            ]
        return [PTextDelta("完成"), Done()]

    agent = Agent(FakeProvider(handler), create_registry(), session, gate=_gate(tmp_path))

    async def go():
        events = []
        async for event in agent.run("执行"):
            events.append(event)
            if isinstance(event, PermissionAskEvent):
                agent.answer_permission(True)
        return events

    events = asyncio.run(go())
    asks = [e for e in events if isinstance(e, PermissionAskEvent)]
    assert len(asks) == 1
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert results[0].result.ok is True


# ----------------------------------------------------------------------
# allowed_tools 过滤（F7）
# ----------------------------------------------------------------------


def test_allowed_tools_filters_visible_tools(tmp_path):
    seen: list[set[str]] = []

    def handler(n, messages, tools):
        seen.append({t.name for t in tools})
        return [PTextDelta("好"), Done()]

    agent = Agent(
        FakeProvider(handler),
        create_registry(),
        ChatSession(),
        allowed_tools={"read_file"},
    )
    _collect(agent, "看看")
    assert seen and all(toolset == {"read_file"} for toolset in seen)


# ----------------------------------------------------------------------
# 读缓存实例隔离（F5）
# ----------------------------------------------------------------------


def test_read_cache_isolated_between_agents(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    cache_a = FileReadCache()
    cache_b = FileReadCache()
    session_a = ChatSession()
    session_b = ChatSession()

    def handler(n, messages, tools):
        return [
            ToolCall(id=f"t{n}", name="read_file", args={"file_path": str(f)}),
            Done(),
        ] if n == 1 else [PTextDelta("完"), Done()]

    gate = PermissionGate(tmp_path.resolve(), RuleSet(), Mode.ACCEPT)
    agent_a = Agent(FakeProvider(handler), create_registry(), session_a, read_cache=cache_a, gate=gate)
    agent_b = Agent(FakeProvider(handler), create_registry(), session_b, read_cache=cache_b, gate=gate)
    _collect(agent_a, "读")
    _collect(agent_b, "读")
    mtime = f.stat().st_mtime
    # 各自缓存独立：A 有记录、B 也有自己的记录（互不可见）
    assert cache_a.get(str(f), mtime) == "x = 1\n"
    assert cache_b.get(str(f), mtime) == "x = 1\n"
    # 独立性：清空 A 不影响 B
    cache_a._store.clear()
    assert cache_b.get(str(f), mtime) == "x = 1\n"


def test_agent_run_call_injects_context(tmp_path):
    """工具执行线程内能取到本 Agent 的 ParentContext（缓存与 Agent 引用）。"""
    f = tmp_path / "b.py"
    f.write_text("y = 2\n", encoding="utf-8")
    cache = FileReadCache()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="read_file", args={"file_path": str(f)}),
                Done(),
            ]
        return [PTextDelta("完"), Done()]

    agent = Agent(
        FakeProvider(handler),
        create_registry(),
        ChatSession(),
        read_cache=cache,
        gate=PermissionGate(tmp_path.resolve(), RuleSet(), Mode.ACCEPT),
    )
    _collect(agent, "读")
    # 经 Agent 读过后缓存有记录（注入链路闭环）
    assert cache.get(str(f), f.stat().st_mtime) == "y = 2\n"
    # 无上下文时直读行为不变
    assert ReadFileTool().execute({"file_path": str(f)}).content == "y = 2\n"
    clear()  # 清理测试线程上下文（本测试未 push，无操作）
