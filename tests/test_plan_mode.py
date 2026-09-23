"""Plan Mode 测试：enter/exit 幂等、系统级指令注入、工具收窄。"""

from mewcode.agent.loop import Agent
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    """占位 Provider：本文件测试不发起请求。"""

    def stream(self, messages, tools, system):
        raise AssertionError("本测试不应调用 provider")
        yield  # pragma: no cover


def _agent():
    session = ChatSession()
    return Agent(FakeProvider(), create_registry(), session), session


def test_enter_plan_mode_sets_flag_without_session_messages():
    """ch06 迁移：/plan 只置标志，不再往会话写 user 消息（F4）。"""
    agent, session = _agent()
    agent.enter_plan_mode()
    agent.enter_plan_mode()  # 幂等：重复进入无副作用
    assert agent.plan_mode is True
    assert session.messages == []


def test_exit_plan_mode_clears_flag_without_session_messages():
    agent, session = _agent()
    agent.enter_plan_mode()
    agent.exit_plan_mode()
    agent.exit_plan_mode()  # 幂等：重复退出无副作用
    assert agent.plan_mode is False
    assert session.messages == []


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

from mewcode.commands.builtins import build_registry
from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.ui import StatusInfo, TokenStats, UiController


class FakeUi(UiController):
    """记录计划模式切换与展示的最小替身（其余接口方法用不到）。"""

    def __init__(self) -> None:
        self.displayed: list[str] = []
        self.entered = 0
        self.exited = 0
        self.mode = False

    def display(self, text: str) -> None:
        self.displayed.append(text)

    def enter_plan_mode(self) -> None:
        self.entered += 1
        self.mode = True

    def exit_plan_mode(self) -> None:
        self.exited += 1
        self.mode = False

    def status_info(self) -> StatusInfo:
        return StatusInfo(
            config_name="", model="", thinking=False, permission_label="",
            plan_mode=self.mode, tokens=TokenStats(),
        )


def _run(text: str, ui: FakeUi) -> CommandResult:
    return dispatch(build_registry(), ParsedCommand(name=text[1:], args=""), ui)


def test_plan_command_enters_mode():
    ui = FakeUi()
    result = _run("/plan", ui)
    assert result is CommandResult.CONTINUE
    assert ui.entered == 1
    assert any("计划模式" in t for t in ui.displayed)


def test_plan_command_idempotent_feedback():
    ui = FakeUi()
    ui.mode = True
    result = _run("/plan", ui)
    assert result is CommandResult.CONTINUE
    assert ui.entered == 0  # 已在模式：不再调用 enter
    assert any("已在计划模式" in t for t in ui.displayed)


def test_do_command_exits_mode():
    ui = FakeUi()
    ui.mode = True
    result = _run("/do", ui)
    assert result is CommandResult.CONTINUE
    assert ui.exited == 1
    assert any("切回执行模式" in t for t in ui.displayed)


def test_do_command_without_plan_mode():
    ui = FakeUi()
    result = _run("/do", ui)
    assert result is CommandResult.CONTINUE
    assert ui.exited == 0
    assert any("不在计划模式" in t for t in ui.displayed)


def test_help_lists_plan_and_do():
    ui = FakeUi()
    _run("/help", ui)
    feedback = ui.displayed[0]
    assert "/plan" in feedback and "/do" in feedback


# ---------------------------------------------------------------------------
# 计划模式执行拦截：副作用工具不执行
# ---------------------------------------------------------------------------

import asyncio

from mewcode.agent.events import RoundEnd, RoundEndReason, ToolResultEvent
from mewcode.agent.loop import Agent
from mewcode.provider.base import Done, TextDelta, ToolCall
from mewcode.prompt import PLAN_MODE_DIRECTIVE
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class ScriptedProvider:
    """脚本化 Provider：handler(第几次调用, messages, tools) → 事件列表。

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

    provider = ScriptedProvider(handler)
    agent = Agent(provider, create_registry(), session)
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
    # 消息序：用户问题 → assistant → 工具结果 → 最终 assistant（无注入消息）
    assert "计划模式已禁用" in session.messages[2].tool_results[0].content
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.OK
    # 第 1 轮系统补充块注入完整 Plan 指令（首轮完整）
    assert PLAN_MODE_DIRECTIVE.full_text in provider.systems[0].dynamic
    assert PLAN_MODE_DIRECTIVE.slim_text not in provider.systems[0].dynamic


def test_plan_mode_directive_cadence(tmp_path):
    """注入节奏（F4）：第 1 轮完整、第 2/3 轮精简、第 4 轮完整、第 5 轮精简。"""
    target = tmp_path / "loop.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        # 5 轮全部调用工具，逼出 5 次 LLM 请求
        return [
            ToolCall(id=f"t{n}", name="read_file", args={"file_path": str(target)}),
            Done(),
        ]

    provider = ScriptedProvider(handler)
    agent = Agent(provider, create_registry(), session, max_iterations=5)
    agent.enter_plan_mode()

    async def go():
        return [e async for e in agent.run("调研")]

    events = asyncio.run(go())
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.LIMIT
    assert len(provider.systems) == 5
    full = PLAN_MODE_DIRECTIVE.full_text
    slim = PLAN_MODE_DIRECTIVE.slim_text
    expected = [full, slim, slim, full, slim]
    for system, want in zip(provider.systems, expected):
        assert want in system.dynamic
    # 稳定部分五轮逐字节一致（缓存前缀不变）
    assert len({s.stable for s in provider.systems}) == 1
    # 会话干净：无任何注入消息
    assert len(session.messages) == 11  # user + 5×(assistant + 工具结果)


def test_plan_directive_absent_after_exit(tmp_path):
    """退出计划模式后，补充块不再携带 Plan 指令（F4）。"""
    target = tmp_path / "g.py"
    target.write_text("x = 1\n", encoding="utf-8")
    session = ChatSession()

    def handler(n, messages, tools):
        if n == 1:
            return [
                ToolCall(id="t1", name="read_file", args={"file_path": str(target)}),
                Done(),
            ]
        return [TextDelta("完成"), Done()]

    provider = ScriptedProvider(handler)
    agent = Agent(provider, create_registry(), session)
    agent.enter_plan_mode()
    agent.exit_plan_mode()

    async def go():
        return [e async for e in agent.run("干活")]

    events = asyncio.run(go())
    assert isinstance(events[-1], RoundEnd) and events[-1].reason is RoundEndReason.OK
    for system in provider.systems:
        assert "Plan Mode" not in system.dynamic
    # 会话：用户问题 + assistant + 工具结果 + 最终 assistant，无注入消息
    assert len(session.messages) == 4
