"""工具注册中心：集中登记、按名查找。

wire 格式转换（Anthropic/OpenAI 的工具列表）在 provider 层完成，
注册中心只提供 Tool 对象列表。
"""

from __future__ import annotations

from mewcode.tools.base import Tool, ToolError


class ToolRegistry:
    """集中登记工具，按名查找。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """登记一个工具；重名抛 ToolError。"""
        if tool.name in self._tools:
            raise ToolError(f"工具重名: {tool.name!r} 已被注册")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        """按名查找；找不到抛 ToolError。"""
        tool = self._tools.get(name)
        if tool is None:
            registered = ", ".join(sorted(self._tools)) or "（空）"
            raise ToolError(f"未知工具: {name!r}，已注册工具: {registered}")
        return tool

    def list(self) -> list[Tool]:
        """返回全部工具（按名排序，保证工具列表稳定）。"""
        return [self._tools[n] for n in sorted(self._tools)]
