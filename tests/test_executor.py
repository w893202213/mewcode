"""execute_call 测试：单工具执行全路径（不含回灌，回灌由 Agent 统一控制）。"""

from mewcode.agent.executor import execute_call
from mewcode.provider.base import ToolCall
from mewcode.tools import create_registry


def test_parse_error_returns_without_executing():
    call = ToolCall(id="toolu_1", name="read_file", args={}, parse_error="工具参数 JSON 解析失败: x")
    result = execute_call(create_registry(), call)
    assert not result.ok
    assert "解析失败" in result.content


def test_unknown_tool_returns_error():
    call = ToolCall(id="toolu_1", name="no_such_tool", args={})
    result = execute_call(create_registry(), call)
    assert not result.ok
    assert "未知工具" in result.content
    assert "no_such_tool" in result.content  # 错误信息含未知工具名


def test_known_tool_executes_normally(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("你好", encoding="utf-8")
    call = ToolCall(id="toolu_1", name="read_file", args={"file_path": str(target)})
    result = execute_call(create_registry(), call)
    assert result.ok
    assert "你好" in result.content


def test_tool_error_returned_as_failure():
    call = ToolCall(
        id="toolu_1", name="read_file", args={"file_path": "/不存在的文件"}
    )
    result = execute_call(create_registry(), call)
    assert not result.ok
    assert "文件不存在" in result.content


def test_run_command_executes_directly_without_confirmation(tmp_path):
    """ch05 F6：循环中命令工具不弹确认直接执行。"""
    target = tmp_path / "x.txt"
    call = ToolCall(
        id="toolu_1", name="run_command", args={"command": f"echo 直接跑 > {target}"}
    )
    result = execute_call(create_registry(), call)
    assert result.ok
    assert target.read_text(encoding="utf-8").strip() == "直接跑"


def test_run_command_confirmation_marker_kept_for_future():
    """requires_confirmation 标记保留，留给后续安全章节接管（F6）。"""
    assert create_registry().get("run_command").requires_confirmation is True
