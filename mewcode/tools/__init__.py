"""工具系统：统一 Tool 接口、六个核心工具与注册中心。"""

from mewcode.tools.base import Tool, ToolError, ToolResult, run_tool
from mewcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from mewcode.tools.registry import ToolRegistry
from mewcode.tools.search import FindFilesTool, GrepCodeTool
from mewcode.tools.shell import RunCommandTool


def create_registry(agent_tool: Tool | None = None) -> ToolRegistry:
    """创建并登记六个核心工具的注册中心。

    agent_tool（ch16）：装配层传入 AgentTool 实例才注册——缺省不注册，
    存量测试与无委派场景行为不变。
    """
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
    if agent_tool is not None:
        registry.register(agent_tool)
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
