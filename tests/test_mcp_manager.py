"""MCP 管理层测试：真实 mock 子进程下的多 Server 发现注册、隔离与关闭。"""

import sys
from pathlib import Path

import pytest

from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.manager import McpManager
from mewcode.tools.base import Tool, ToolResult, run_tool
from mewcode.tools.registry import ToolRegistry

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _stdio_config(name: str, env: dict | None = None) -> McpServerConfig:
    """指向 mock MCP Server 的 stdio 配置。"""
    return McpServerConfig(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=[str(SCRIPTS / "mock_mcp_server.py")],
        env=env or {},
    )


def _bad_config() -> McpServerConfig:
    return McpServerConfig(name="bad", transport="stdio", command="/no/such/cmd")


class _StubTool(Tool):
    """替身内置工具（重名测试用）。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.description = "替身工具"
        self.input_schema = {"type": "object", "properties": {}}
        self.read_only = True

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("stub")


def test_discover_and_register_mock_server():
    """mock Server 的三个工具全部注册，且可端到端调用。"""
    manager = McpManager([_stdio_config("demo")])
    registry = ToolRegistry()
    count = manager.discover_and_register(registry)
    assert count == 3
    assert [t.name for t in registry.list()] == ["add", "echo", "fail"]
    # 经 run_tool 统一包装执行（与内置工具同链路）
    result = run_tool(registry.get("add"), {"a": 1, "b": 2})
    assert result.ok and "1 + 2 = 3" in result.content
    # 远端 isError 工具 → 失败结果
    result = run_tool(registry.get("fail"), {})
    assert result.ok is False and "故意失败" in result.content
    manager.shutdown()


def test_bad_server_skipped_others_continue(capsys):
    """一个 Server 起不来：打印中文警告，其余 Server 照常注册。"""
    manager = McpManager([_bad_config(), _stdio_config("good")])
    registry = ToolRegistry()
    count = manager.discover_and_register(registry)
    assert count == 3  # 好 Server 的三个工具全注册
    err = capsys.readouterr().err
    assert "警告" in err
    assert "MCP Server 'bad' 启动失败" in err
    manager.shutdown()


def test_conflict_with_builtin_skipped(capsys):
    """MCP 工具与内置工具重名：跳过该工具并警告，内置不受影响。"""
    registry = ToolRegistry()
    registry.register(_StubTool("add"))  # 替身内置工具占名
    manager = McpManager([_stdio_config("demo")])
    count = manager.discover_and_register(registry)
    assert count == 2  # echo/fail 注册，add 跳过
    assert [t.name for t in registry.list()] == ["add", "echo", "fail"]
    assert registry.get("add").description == "替身工具"  # 内置未被覆盖
    err = capsys.readouterr().err
    assert "工具 'add' 与已注册工具重名" in err
    manager.shutdown()


def test_conflict_between_servers_declaration_order(capsys):
    """两 Server 同名工具：声明顺序先注册优先，后到跳过；各自独有工具照常。"""
    manager = McpManager(
        [
            _stdio_config("first"),
            _stdio_config("second", env={"MOCK_EXTRA_TOOL": "1"}),
        ]
    )
    registry = ToolRegistry()
    count = manager.discover_and_register(registry)
    assert count == 4  # first: echo/add/fail；second: 仅 upper（echo 重名跳过）
    assert [t.name for t in registry.list()] == ["add", "echo", "fail", "upper"]
    assert "工具 'echo' 与已注册工具重名" in capsys.readouterr().err
    # 后到 Server 的独有工具可用
    result = run_tool(registry.get("upper"), {"text": "abc"})
    assert result.ok and result.content == "ABC"
    manager.shutdown()


def test_shutdown_kills_subprocesses_and_idempotent():
    """shutdown 后 stdio 子进程全部终止；重复调用无害。"""
    manager = McpManager([_stdio_config("demo")])
    registry = ToolRegistry()
    manager.discover_and_register(registry)
    procs = [session._transport._proc for session in manager._sessions]
    assert all(proc is not None and proc.returncode is None for proc in procs)
    manager.shutdown()
    assert all(proc.returncode is not None for proc in procs)  # 无残留进程
    manager.shutdown()  # 幂等：不抛异常


def test_no_servers_is_noop():
    """空 Server 列表：发现注册为空操作，shutdown 无害。"""
    manager = McpManager([])
    registry = ToolRegistry()
    assert manager.discover_and_register(registry) == 0
    assert registry.list() == []
    manager.shutdown()
