"""Hook 与 Agent 主循环的接入测试（ch15 F2/F4/F5）：生命周期事件时机、
拦截回灌拒绝原因、注入提示词进下一次请求且一次性消费。"""

import asyncio
import json
from pathlib import Path

from mewcode.agent.events import PermissionAskEvent, RoundEndReason, ToolResultEvent
from mewcode.agent.loop import Agent
from mewcode.hooks.loader import HookAction, HookRule
from mewcode.hooks.manager import HookManager
from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.provider.base import Done, TextDelta as PTextDelta, ToolCall
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。

    systems 记录每次请求的系统提示，供断言注入文本（与 test_agent_loop 同款）。
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


def _rule(event: str, run: str = "", prompt: str = "", index: int = 1) -> HookRule:
    action = (
        HookAction(type="command", run=run)
        if run
        else HookAction(type="inject", prompt=prompt)
    )
    return HookRule(
        index=index, event=event, conditions=[], match="", action=action
    )


def _accept_gate() -> PermissionGate:
    """全放行闸门：让工具走到 Hook 层（闸门放行路径）。"""
    return PermissionGate(Path.cwd().resolve(), RuleSet(), Mode.ACCEPT)


# ----------------------------------------------------------------------
# 生命周期事件时机（F2）
# ----------------------------------------------------------------------


def test_lifecycle_events_fire_with_correct_context(tmp_path):
    events = (
        "round_start",
        "user_message",
        "assistant_message",
        "tool_before",
        "tool_after",
        "round_end",
    )
    marks = {name: tmp_path / f"{name}.json" for name in events}
    manager = HookManager(
        [_rule(name, f"cat > {marks[name]}", index=i) for i, name in enumerate(events, 1)]
    )
    target = tmp_path / "x.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                PTextDelta("读文件"),
                ToolCall(id="t1", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]
        return [PTextDelta("完成"), Done()]

    result_events = _collect(
        Agent(
            FakeProvider(handler),
            create_registry(),
            session,
            # 闸门以 tmp_path 为沙箱根：read_file 放行走 ALLOW 分支 → tool_before
            gate=PermissionGate(tmp_path.resolve(), RuleSet(), Mode.ACCEPT),
            hook_manager=manager,
        ),
        "读一下",
    )
    for name in events:
        assert marks[name].exists(), f"事件 {name} 未触发"
    # 上下文字段：轮计数 / 工具名 / 工具结果 / 结束原因
    assert json.loads(marks["round_start"].read_text())["round"] == 1
    assert json.loads(marks["tool_before"].read_text())["tool"] == "read_file"
    assert json.loads(marks["tool_after"].read_text())["result"]["ok"] is True
    assert json.loads(marks["round_end"].read_text())["reason"] == "ok"
    assert result_events[-1].reason is RoundEndReason.OK


def test_round_counter_increments_per_run(tmp_path):
    log = tmp_path / "rounds.log"
    # 每条 stdin JSON 后补换行，避免两次追加连成一行
    manager = HookManager([_rule("round_start", f"cat >> {log}; echo >> {log}")])
    session = ChatSession()
    provider = FakeProvider(lambda n, m, t: [PTextDelta("好"), Done()])
    agent = Agent(provider, create_registry(), session, hook_manager=manager)
    _collect(agent, "第一次")
    _collect(agent, "第二次")
    rounds = [
        json.loads(line)["round"]
        for line in log.read_text().splitlines()
    ]
    assert rounds == [1, 2]


# ----------------------------------------------------------------------
# 拦截回灌（F4）
# ----------------------------------------------------------------------


def test_tool_before_intercept_feeds_back_reason(tmp_path):
    mark = tmp_path / "executed"
    manager = HookManager([_rule("tool_before", "echo Hook已拦截; exit 1")])
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(
                    id="t1",
                    name="run_command",
                    args={"command": f"touch {mark}"},
                ),
                Done(),
            ]
        # 模型收到失败结果后调整行为
        return [PTextDelta("改用其他方式，任务完成"), Done()]

    result_events = _collect(
        Agent(
            FakeProvider(handler),
            create_registry(),
            session,
            gate=_accept_gate(),
            hook_manager=manager,
        ),
        "执行任务",
    )
    tool_results = [e for e in result_events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    result = tool_results[0].result
    assert result.ok is False
    assert "Hook已拦截" in result.content
    assert not mark.exists()  # 工具被拦截，未执行
    assert result_events[-1].reason is RoundEndReason.OK  # 模型调整后正常结束
    # 会话记录该失败结果（is_error=True，供模型下轮读取）
    assert session.messages[2].tool_results[0].is_error is True


# ----------------------------------------------------------------------
# 注入提示词（F5）
# ----------------------------------------------------------------------


def test_inject_appears_in_next_request_once(tmp_path):
    manager = HookManager(
        [_rule("tool_after", prompt="【注入】注意安全")]
    )
    target = tmp_path / "x.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()
    provider = FakeProvider(
        lambda n, m, t: [
            ToolCall(id=f"t{n}", name="read_file", args={"file_path": str(target)}),
            Done(),
        ]
        if n == 1
        else [PTextDelta("完成"), Done()]
    )
    agent = Agent(provider, create_registry(), session, hook_manager=manager)
    _collect(agent, "读文件")
    # 第一次请求发出时注入尚未产生（tool_after 在请求之后触发）
    assert "【注入】注意安全" not in provider.systems[0].dynamic
    # 第二次请求含注入文本（下一次请求一次性消费）
    assert "【注入】注意安全" in provider.systems[1].dynamic
    assert manager.take_injects() == []  # 已全取全清
    # 第三轮请求不再出现（无下一次消费机会时文本自然丢弃）
    _collect(agent, "再读一次")
    assert "【注入】注意安全" not in provider.systems[2].dynamic


def test_ask_allow_path_also_goes_through_hook(tmp_path):
    """人工应答放行（ASK 路径）同样过 tool_before Hook——补充策略不旁路。

    回归：e2e 曾发现 y 应答放行绕过 Hook 拦截（文件被删除）。
    """
    mark = tmp_path / "executed"
    manager = HookManager([_rule("tool_before", "echo Hook已拦截; exit 1")])
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(
                    id="t1",
                    name="run_command",
                    args={"command": f"touch {mark}"},
                ),
                Done(),
            ]
        return [PTextDelta("调整完成"), Done()]

    # default 档：run_command（副作用）走 ASK 询问，应答放行后进 Hook 层
    gate = PermissionGate(tmp_path.resolve(), RuleSet(), Mode.DEFAULT)
    agent = Agent(
        FakeProvider(handler),
        create_registry(),
        session,
        gate=gate,
        hook_manager=manager,
    )

    async def go():
        events = []
        async for event in agent.run("执行任务"):
            events.append(event)
            if isinstance(event, PermissionAskEvent):
                agent.answer_permission(True)
        return events

    events = asyncio.run(go())
    tool_results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(tool_results) == 1
    assert "Hook已拦截" in tool_results[0].result.content
    assert not mark.exists()  # 人工放行后被 Hook 拦截，工具未执行
    assert events[-1].reason is RoundEndReason.OK


def test_no_hook_manager_behavior_unchanged():
    """hook_manager=None：零开销，事件流与接入前一致。"""
    session = ChatSession()
    events = _collect(
        Agent(
            FakeProvider(lambda n, m, t: [PTextDelta("你好"), Done()]),
            create_registry(),
            session,
        ),
        "问好",
    )
    assert [type(e).__name__ for e in events] == [
        "ProgressEvent",
        "TextDelta",
        "UsageEvent",
        "RoundEnd",
    ]
    assert events[-1].reason is RoundEndReason.OK
