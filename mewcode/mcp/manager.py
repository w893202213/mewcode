"""MCP 管理层：多 Server 生命周期编排、发现注册与统一关闭。

discover_and_register 按配置声明顺序对每个 Server 并行「连接 + 握手 +
列工具」，工具逐条包装注册进 ToolRegistry；任何一步失败打印中文警告
跳过该 Server（不影响其余），方法本身不抛异常。重名策略：内置六工具
优先，MCP 工具之间先注册优先，重名跳过并警告。shutdown 统一关闭全部
会话（终止子进程、停循环线程），幂等可重入。
"""

from __future__ import annotations

import sys
import threading

from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.session import McpSession, ToolSpec
from mewcode.mcp.tool_adapter import McpTool, tool_name_valid
from mewcode.tools.base import ToolError
from mewcode.tools.registry import ToolRegistry

# 单个 Server 发现（含启动与列工具）的整体等待上限（秒）
_DISCOVER_TIMEOUT = 30.0


def _warn(message: str) -> None:
    """启动期警告：打印到 stderr，不进 TUI。"""
    print(f"警告: {message}", file=sys.stderr)


class McpManager:
    """多 Server 管理：发现注册与统一关闭。"""

    def __init__(self, servers: list[McpServerConfig]) -> None:
        self._servers = list(servers)
        self._sessions: list[McpSession] = []

    def discover_and_register(self, registry: ToolRegistry) -> int:
        """发现全部 Server 的工具并注册进 registry，返回注册工具总数。

        单个 Server 的任何失败（子进程起不来、握手失败、tools/list
        出错、工具名不合法、重名）都只警告并跳过，不抛异常。
        """
        results: list[tuple[McpServerConfig, McpSession | None, object]] = [
            (cfg, None, None) for cfg in self._servers
        ]
        threads = [
            threading.Thread(target=self._discover_one, args=(i, cfg, results))
            for i, cfg in enumerate(self._servers)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=_DISCOVER_TIMEOUT)

        count = 0
        for cfg, session, payload in results:
            if session is None:
                reason = payload or "发现超时"
                _warn(f"MCP Server {cfg.name!r} 启动失败: {reason}，已跳过")
                continue
            tools = payload if isinstance(payload, list) else []
            for spec in tools:
                if not isinstance(spec, ToolSpec):
                    continue
                if not tool_name_valid(spec.name):
                    _warn(
                        f"MCP Server {cfg.name!r} 的工具 {spec.name!r} 名字不合法"
                        "（需 1-64 位字母/数字/下划线/连字符），已跳过"
                    )
                    continue
                try:
                    registry.register(McpTool(spec, session))
                except ToolError:
                    _warn(
                        f"MCP Server {cfg.name!r} 的工具 {spec.name!r} "
                        "与已注册工具重名，已跳过"
                    )
                    continue
                count += 1
            self._sessions.append(session)
        return count

    def _discover_one(
        self,
        index: int,
        cfg: McpServerConfig,
        results: list[tuple[McpServerConfig, McpSession | None, object]],
    ) -> None:
        """单 Server 发现（线程内）：连接 + 握手 + 列工具；失败记录原因。"""
        session = McpSession(cfg)
        try:
            session.start()
            tools = session.list_tools()
        except Exception as e:  # 任何失败都不影响其他 Server
            session.close_sync()  # 回收失败会话的循环线程
            results[index] = (cfg, None, e)
            return
        results[index] = (cfg, session, tools)

    def shutdown(self) -> None:
        """关闭全部会话：单个关闭失败不中断其余；幂等可重入。"""
        sessions, self._sessions = self._sessions, []
        for session in sessions:
            try:
                session.close_sync()
            except Exception:
                pass  # 关闭过程中的错误不影响其余会话与进程退出
