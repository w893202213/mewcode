"""单工具执行：按名查工具 → 执行，返回结构化结果。

不弹确认（ch05 F6：Agent Loop 中命令直接执行，确认机制留给后续
安全章节）；不写会话——回灌顺序由 Agent 统一控制（N7）。
"""

from __future__ import annotations

from mewcode.provider.base import ToolCall
from mewcode.tools.base import ToolError, ToolResult, run_tool
from mewcode.tools.registry import ToolRegistry


def execute_call(registry: ToolRegistry, call: ToolCall) -> ToolResult:
    """执行一个工具调用，返回结构化结果（成功内容或中文错误信息）。

    任何失败路径（参数解析失败、未知工具、执行错误）都不抛异常，
    而是返回 ok=False 的结果。
    """
    if call.parse_error:
        return ToolResult.failure(call.parse_error)

    try:
        tool = registry.get(call.name)
    except ToolError as e:
        return ToolResult.failure(str(e))

    return run_tool(tool, call.args)
