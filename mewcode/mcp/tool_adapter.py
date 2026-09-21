"""MCP 工具适配层：把远端工具包装成 MewCode 统一的 Tool 接口。

每个 McpTool 实例对应一个远端工具：元信息取自 tools/list 结果，
execute 经会话的同步桥接转发到远端。注册进 ToolRegistry 后，
Agent 循环、provider 转换、权限闸门全走现有链路，对 MCP 无感。
"""

from __future__ import annotations

import re

from mewcode.mcp.session import McpSession, ToolSpec
from mewcode.tools.base import DEFAULT_TIMEOUT, Tool, ToolResult

# MCP 规范工具名约束（与 Anthropic 工具名规则一致）：
# 1-64 位，只含字母、数字、下划线、连字符
_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# 缺省输入 Schema（远端未声明时兜底）
_DEFAULT_SCHEMA = {"type": "object", "properties": {}}


def tool_name_valid(name: str) -> bool:
    """工具名是否合法（注册前校验，防止 provider 层请求整体失败）。"""
    return _TOOL_NAME_PATTERN.fullmatch(name) is not None


class McpTool(Tool):
    """一个远端 MCP 工具的适配：实现统一 Tool 接口。

    read_only=False（外部工具副作用未知，交给权限闸门裁决）；
    timeout 沿用默认 30 秒（会话桥接的 25 秒超时先触发）。
    """

    def __init__(self, spec: ToolSpec, session: McpSession) -> None:
        self.name = spec.name
        self.description = spec.description
        self.input_schema = spec.input_schema or dict(_DEFAULT_SCHEMA)
        self.read_only = False
        self.timeout = DEFAULT_TIMEOUT
        self._session = session

    def execute(self, args: dict) -> ToolResult:
        """把参数转发给远端工具；远端错误/超时/连接失败转中文错误结果。"""
        try:
            content = self._session.call_tool_sync(self.name, args)
        except Exception as e:
            return ToolResult.failure(f"MCP 工具 {self.name!r} 调用失败: {e}")
        return ToolResult.success(content)
