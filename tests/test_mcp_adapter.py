"""MCP 工具适配层测试：McpTool 的 Tool 接口与 ToolResult 转换、名校验。

会话用 stub（不启动事件循环线程），模拟成功/远端错误/超时/连接失败
四种远端行为；另验证 McpTool 经 run_tool 统一包装后可注册可用。
"""

import pytest

from mewcode.mcp.protocol import McpServerError
from mewcode.mcp.session import ToolSpec
from mewcode.mcp.tool_adapter import McpTool, tool_name_valid
from mewcode.tools.base import ToolResult, run_tool
from mewcode.tools.registry import ToolRegistry


class StubSession:
    """会话 stub：按预设行为返回或抛错。"""

    def __init__(self, behavior) -> None:
        self._behavior = behavior
        self.calls: list[tuple] = []

    def call_tool_sync(self, name: str, args: dict) -> str:
        self.calls.append((name, args))
        if isinstance(self._behavior, Exception):
            raise self._behavior
        return self._behavior


def _tool(behavior) -> McpTool:
    spec = ToolSpec(
        name="echo",
        description="回显文本",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
    )
    return McpTool(spec, StubSession(behavior))


def test_execute_success():
    tool = _tool("你好")
    result = tool.execute({"text": "x"})
    assert result == ToolResult(ok=True, content="你好")
    assert tool._session.calls == [("echo", {"text": "x"})]  # 参数原样转发


def test_execute_remote_error():
    tool = _tool(McpServerError("MCP 工具 'echo' 执行失败: 参数非法"))
    result = tool.execute({})
    assert result.ok is False
    assert "参数非法" in result.content


def test_execute_connection_failure():
    tool = _tool(McpServerError("MCP 连接已损坏，无法继续调用"))
    result = tool.execute({})
    assert result.ok is False
    assert "连接已损坏" in result.content


def test_execute_unexpected_exception():
    tool = _tool(RuntimeError("boom"))
    result = tool.execute({})
    assert result.ok is False
    assert "boom" in result.content


def test_tool_interface_metadata():
    """元信息取自 ToolSpec；read_only=False；timeout 默认 30 秒。"""
    tool = _tool("")
    assert tool.name == "echo"
    assert tool.description == "回显文本"
    assert tool.input_schema["type"] == "object"
    assert tool.read_only is False
    assert tool.timeout == 30.0


def test_default_schema_when_missing():
    """远端未声明 inputSchema 时用兜底 schema。"""
    spec = ToolSpec(name="bare")
    tool = McpTool(spec, StubSession(""))
    assert tool.input_schema == {"type": "object", "properties": {}}


def test_registers_in_registry_and_runs_via_run_tool():
    """注册进 ToolRegistry 后经 run_tool 统一包装执行——与内置工具同链路。"""
    registry = ToolRegistry()
    registry.register(_tool("ok"))
    tool = registry.get("echo")
    result = run_tool(tool, {"text": "hi"})
    assert result.ok and result.content == "ok"


@pytest.mark.parametrize(
    "name,expected",
    [
        ("echo", True),
        ("my_tool", True),
        ("tool-2", True),
        ("A" * 64, True),
        ("", False),
        ("A" * 65, False),
        ("dot.name", False),  # 含点不合法（MCP 规范）
        ("工具", False),  # 非 ASCII 不合法
        ("a b", False),
    ],
)
def test_tool_name_valid(name, expected):
    assert tool_name_valid(name) is expected
