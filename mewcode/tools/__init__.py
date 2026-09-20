"""工具系统：统一 Tool 接口、六个核心工具与注册中心。"""

from mewcode.tools.base import Tool, ToolError, ToolResult, run_tool
from mewcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from mewcode.tools.registry import ToolRegistry
from mewcode.tools.search import FindFilesTool, GrepCodeTool
from mewcode.tools.shell import RunCommandTool


def create_registry() -> ToolRegistry:
    """创建并登记六个核心工具的注册中心。"""
    registry = ToolRegistry()
    for tool in (
        ReadFileTool(),
        WriteFileTool(),
        EditFileTool(),
        RunCommandTool(),
        FindFilesTool(),
        GrepCodeTool(),
    ):
        registry.register(tool)
    return registry


__all__ = [
    "Tool",
    "ToolError",
    "ToolResult",
    "ToolRegistry",
    "run_tool",
    "ReadFileTool",
    "WriteFileTool",
    "EditFileTool",
    "RunCommandTool",
    "FindFilesTool",
    "GrepCodeTool",
    "create_registry",
]
