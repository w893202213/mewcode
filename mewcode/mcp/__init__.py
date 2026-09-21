"""MCP 客户端：发现、连接外部 MCP Server 并注册其工具。

公开面：配置（McpServerConfig/load_mcp_servers）、会话（McpSession）、
适配（McpTool）、管理（McpManager）与统一错误（McpServerError）。
"""

from mewcode.mcp.config import McpServerConfig, load_mcp_servers
from mewcode.mcp.manager import McpManager
from mewcode.mcp.protocol import McpServerError
from mewcode.mcp.session import McpSession, ToolSpec
from mewcode.mcp.tool_adapter import McpTool

__all__ = [
    "McpServerConfig",
    "McpServerError",
    "McpManager",
    "McpSession",
    "McpTool",
    "ToolSpec",
    "load_mcp_servers",
]
