"""Plan Mode 测试：enter/exit 幂等、提示注入、工具收窄。"""

from mewcode.agent.loop import PLAN_MODE_OFF_MESSAGE, PLAN_MODE_ON_MESSAGE, Agent
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    """占位 Provider：本文件测试不发起请求。"""

    def stream(self, messages, tools):
        raise AssertionError("本测试不应调用 provider")
        yield  # pragma: no cover


def _agent():
    session = ChatSession()
    return Agent(FakeProvider(), create_registry(), session), session


def test_enter_plan_mode_injects_prompt_once():
    agent, session = _agent()
    agent.enter_plan_mode()
    agent.enter_plan_mode()  # 幂等：重复进入不重复注入
    assert agent.plan_mode is True
    assert [m.text for m in session.messages] == [PLAN_MODE_ON_MESSAGE]


def test_exit_plan_mode_injects_prompt_once():
    agent, session = _agent()
    agent.enter_plan_mode()
    agent.exit_plan_mode()
    agent.exit_plan_mode()  # 幂等：重复退出不重复注入
    assert agent.plan_mode is False
    assert [m.text for m in session.messages] == [PLAN_MODE_ON_MESSAGE, PLAN_MODE_OFF_MESSAGE]


def test_exit_without_enter_no_injection():
    agent, session = _agent()
    agent.exit_plan_mode()
    assert agent.plan_mode is False
    assert session.messages == []


def test_visible_tools_all_by_default():
    agent, _ = _agent()
    assert [t.name for t in agent._visible_tools()] == [
        "edit_file",
        "find_files",
        "grep_code",
        "read_file",
        "run_command",
        "write_file",
    ]


def test_visible_tools_readonly_in_plan_mode():
    agent, _ = _agent()
    agent.enter_plan_mode()
    assert [t.name for t in agent._visible_tools()] == [
        "find_files",
        "grep_code",
        "read_file",
    ]


# ---------------------------------------------------------------------------
# T12: /plan /do 命令分发
# ---------------------------------------------------------------------------

from mewcode.tui.commands import CommandResult, handle_command


class FakeAgent:
    """记录 enter/exit 调用的假 Agent。"""

    def __init__(self):
        self.entered = 0
        self.exited = 0
        self.mode = False

    @property
    def plan_mode(self) -> bool:
        return self.mode

    def enter_plan_mode(self) -> None:
        self.entered += 1
        self.mode = True

    def exit_plan_mode(self) -> None:
        self.exited += 1
        self.mode = False


def test_plan_command_enters_mode():
    agent = FakeAgent()
    result, feedback = handle_command("/plan", ChatSession(), agent)
    assert result is CommandResult.CONTINUE
    assert agent.entered == 1
    assert "计划模式" in feedback


def test_plan_command_idempotent_feedback():
    agent = FakeAgent()
    agent.mode = True
    result, feedback = handle_command("/plan", ChatSession(), agent)
    assert result is CommandResult.CONTINUE
    assert agent.entered == 0  # 已在模式：不再调用 enter
    assert "已在计划模式" in feedback


def test_do_command_exits_mode():
    agent = FakeAgent()
    agent.mode = True
    result, feedback = handle_command("/do", ChatSession(), agent)
    assert result is CommandResult.CONTINUE
    assert agent.exited == 1
    assert "切回执行模式" in feedback


def test_do_command_without_plan_mode():
    agent = FakeAgent()
    result, feedback = handle_command("/do", ChatSession(), agent)
    assert result is CommandResult.CONTINUE
    assert agent.exited == 0
    assert "不在计划模式" in feedback


def test_help_lists_plan_and_do():
    from mewcode.tui.commands import COMMAND_NAMES

    assert "/plan" in COMMAND_NAMES and "/do" in COMMAND_NAMES
    _, feedback = handle_command("/help", ChatSession(), FakeAgent())
    assert "/plan" in feedback and "/do" in feedback


# ---------------------------------------------------------------------------
# 计划模式执行拦截：副作用工具不执行
# ---------------------------------------------------------------------------

import asyncio

from mewcode.agent.events import RoundEnd, RoundEndReason, ToolResultEvent
from mewcode.agent.loop import Agent
from mewcode.provider.base import Done, TextDelta, ToolCall
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class ScriptedProvider:
    """脚本化 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0

    def stream(self, messages, tools):
        self.calls += 1
        return iter(self._handler(self.calls, messages, tools))


def test_plan_mode_blocks_side_effect_tools(tmp_path):
    """计划模式下模型调用写工具：不执行、回灌失败结果（F7 只放开读类工具）。"""
    target = tmp_path / "should_not_exist.txt"
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(
                    id="t1", name="run_command", args={"command": f"echo x > {target}"}
                ),
                Done(),
            ]
        return [TextDelta("计划如下…"), Done()]

    agent = Agent(ScriptedProvider(handler), create_registry(), session)
    agent.enter_plan_mode()

    async def go():
        return [e async for e in agent.run("写个文件")]

    events = asyncio.run(go())
    # 副作用工具被拒绝：文件未创建
    assert not target.exists()
    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert len(results) == 1
    assert "计划模式已禁用" in results[0].result.content
    assert results[0].result.ok is False
    # 结果已回灌，模型下一轮能看到拒绝原因
    # 消息序：plan 提示 → 用户问题 → assistant → 工具结果 → 最终 assistant
    assert "计划模式已禁用" in session.messages[3].tool_results[0].content
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.OK
