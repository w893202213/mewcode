"""ToolRunner 测试：执行、确认拦截、回灌全路径。"""

from mewcode.agent.runner import ToolRunner
from mewcode.provider.base import ToolCall
from mewcode.session import ChatSession
from mewcode.tools import create_registry


def _runner(session: ChatSession, confirm=None) -> ToolRunner:
    return ToolRunner(create_registry(), session, confirm=confirm)


def test_unknown_tool_returns_error_and_records():
    s = ChatSession()
    s.add_user("问")
    call = ToolCall(id="toolu_1", name="no_such_tool", args={})
    result = _runner(s).run(call)
    assert not result.ok
    assert "未知工具" in result.content
    # 错误结果回灌会话
    assert len(s.messages) == 2
    rec = s.messages[-1].tool_results[0]
    assert rec.tool_use_id == "toolu_1"
    assert rec.is_error is True


def test_parse_error_returns_without_executing():
    s = ChatSession()
    s.add_user("问")
    call = ToolCall(id="toolu_1", name="read_file", args={}, parse_error="工具参数 JSON 解析失败: x")
    result = _runner(s).run(call)
    assert not result.ok
    assert "解析失败" in result.content
    rec = s.messages[-1].tool_results[0]
    assert rec.is_error is True


def test_confirm_deny_blocks_execution(tmp_path):
    s = ChatSession()
    s.add_user("删掉它")
    call = ToolCall(
        id="toolu_1", name="run_command", args={"command": f"echo 危险 > {tmp_path}/x.txt"}
    )
    result = _runner(s, confirm=lambda cmd: False).run(call)
    assert not result.ok
    assert "用户拒绝了" in result.content
    # 命令确实没执行
    assert not (tmp_path / "x.txt").exists()
    # 拒绝信息回灌
    rec = s.messages[-1].tool_results[0]
    assert "用户拒绝了" in rec.content


def test_confirm_allow_executes(tmp_path):
    s = ChatSession()
    s.add_user("写个文件")
    target = tmp_path / "x.txt"
    call = ToolCall(
        id="toolu_1", name="run_command", args={"command": f"echo 好 > {target}"}
    )
    result = _runner(s, confirm=lambda cmd: True).run(call)
    assert result.ok
    assert target.read_text(encoding="utf-8").strip() == "好"
    rec = s.messages[-1].tool_results[0]
    assert rec.is_error is False


def test_confirm_receives_command_text():
    s = ChatSession()
    s.add_user("跑")
    seen: list[str] = []
    call = ToolCall(id="toolu_1", name="run_command", args={"command": "ls -la"})
    _runner(s, confirm=lambda cmd: seen.append(cmd) or False).run(call)
    assert seen == ["ls -la"]


def test_no_confirm_callback_confirmation_tools_run_directly():
    """confirm 未注入时（如自动化场景）需确认的工具直接执行。"""
    s = ChatSession()
    s.add_user("跑")
    call = ToolCall(id="toolu_1", name="run_command", args={"command": "echo 直接跑"})
    result = _runner(s, confirm=None).run(call)
    assert result.ok
    assert "直接跑" in result.content


def test_tool_error_recorded_as_is_error():
    s = ChatSession()
    s.add_user("读个不存在的文件")
    call = ToolCall(id="toolu_1", name="read_file", args={"file_path": "/不存在的文件"})
    result = _runner(s).run(call)
    assert not result.ok
    assert "文件不存在" in result.content
    rec = s.messages[-1].tool_results[0]
    assert rec.is_error is True
