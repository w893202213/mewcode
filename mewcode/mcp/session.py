"""MCP 会话层：一个 Server 的完整会话——握手、列工具、调工具与同步桥接。

每个会话一个专属 daemon 线程跑独立事件循环，连接（含 stdio 子进程）
绑定其上；同步侧（Tool.execute 所在线程）经 run_coroutine_threadsafe
投递协程并等待结果，故障隔离到单 Server。

三步流程（MCP 规范）：
1. initialize 握手：声明协议版本与能力，校验 Server 返回的版本
2. tools/list 列工具：游标翻页聚合
3. tools/call 调工具：提取 text 内容，isError 转失败
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field

from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.protocol import McpServerError, RpcClient
from mewcode.mcp.transports import create_transport

# 启动时限：connect + initialize + initialized 全流程（秒）
START_TIMEOUT = 10.0
# 工具调用桥接超时：略小于 run_tool 的 30 秒兜底，中文超时消息先回报（秒）
CALL_TIMEOUT = 25.0
# 单个请求的异步超时：略大于桥接超时，让桥接的中文消息先触发（秒）
_RPC_CALL_TIMEOUT = 30.0

# 支持的 MCP 协议版本（2025-03-26 为客户端声明版本）
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


@dataclass
class ToolSpec:
    """tools/list 返回的单个工具定义。"""

    name: str
    description: str = ""
    input_schema: dict = field(default_factory=dict)


class McpSession:
    """一个 MCP Server 的会话：专属线程事件循环 + 同步桥接。"""

    def __init__(self, config: McpServerConfig) -> None:
        self.config = config
        self._transport = create_transport(config)
        self._rpc = RpcClient(self._transport)
        self._loop = asyncio.new_event_loop()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-{config.name}", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------------
    # 事件循环线程与桥接
    # ------------------------------------------------------------------

    def _run_loop(self) -> None:
        """专属线程的事件循环入口。"""
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, timeout: float):
        """把协程投递到专属循环并同步等待结果；超时抛中文 McpServerError。"""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except FutureTimeoutError as e:
            raise McpServerError(f"MCP 操作超时（{timeout:g} 秒）") from e

    def _ensure_open(self) -> None:
        if self._closed:
            raise McpServerError("MCP 会话已关闭")

    # ------------------------------------------------------------------
    # 启动握手
    # ------------------------------------------------------------------

    def start(self) -> None:
        """同步入口：建立连接并完成初始化握手（10 秒时限）。"""
        self._ensure_open()
        self._submit(self._start_async(), START_TIMEOUT)

    async def _start_async(self) -> None:
        await self._rpc.start()
        result = await self._rpc.request(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "mewcode", "version": _client_version()},
            },
            START_TIMEOUT,
        )
        version = result.get("protocolVersion") if isinstance(result, dict) else None
        if version not in SUPPORTED_VERSIONS:
            raise McpServerError(
                f"MCP Server 协议版本不支持: {version!r}（可选: {sorted(SUPPORTED_VERSIONS)}）"
            )
        await self._rpc.notify("notifications/initialized", None)

    # ------------------------------------------------------------------
    # 列出工具
    # ------------------------------------------------------------------

    def list_tools(self) -> list[ToolSpec]:
        """同步入口：列出全部工具（游标翻页聚合）。"""
        self._ensure_open()
        return self._submit(self._list_tools_async(), START_TIMEOUT)

    async def _list_tools_async(self) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        cursor: str | None = None
        while True:
            params: dict = {} if cursor is None else {"cursor": cursor}
            result = await self._rpc.request("tools/list", params, START_TIMEOUT)
            for item in result.get("tools", []) if isinstance(result, dict) else []:
                spec = _parse_tool_spec(item)
                if spec is not None:
                    tools.append(spec)
            next_cursor = result.get("nextCursor") if isinstance(result, dict) else None
            if not next_cursor:
                return tools
            cursor = str(next_cursor)

    # ------------------------------------------------------------------
    # 调用工具
    # ------------------------------------------------------------------

    def call_tool_sync(self, name: str, args: dict) -> str:
        """同步桥接：调用远端工具并返回文本结果；错误/超时抛 McpServerError。"""
        self._ensure_open()
        return self._submit(self._call_tool_async(name, args), CALL_TIMEOUT)

    async def _call_tool_async(self, name: str, args: dict) -> str:
        result = await self._rpc.request(
            "tools/call",
            {"name": name, "arguments": args},
            _RPC_CALL_TIMEOUT,
        )
        if not isinstance(result, dict):
            raise McpServerError(f"MCP 工具 {name!r} 返回格式错误: 不是 JSON 对象")
        texts = _extract_texts(result)
        if result.get("isError"):
            raise McpServerError(f"MCP 工具 {name!r} 执行失败: {texts or '（无详情）'}")
        return texts

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    def close_sync(self) -> None:
        """停止会话：关连接、停循环线程并 join；幂等可重入。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._submit(self._close_async(), 5.0)
        except Exception:
            pass  # 关闭过程中的任何错误（含循环已停）都不影响进程退出
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except RuntimeError:
            pass  # 循环已停止
        self._thread.join(timeout=5.0)

    async def _close_async(self) -> None:
        await self._rpc.close()


def _client_version() -> str:
    """客户端版本号（避免 mcp 包与顶层包的导入循环，惰性读取）。"""
    from mewcode import __version__

    return __version__


def _parse_tool_spec(item: object) -> ToolSpec | None:
    """把 tools/list 的一个条目转成 ToolSpec；非法条目返回 None（跳过）。"""
    if not isinstance(item, dict):
        return None
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    description = item.get("description", "")
    input_schema = item.get("inputSchema", {})
    if not isinstance(description, str):
        description = ""
    if not isinstance(input_schema, dict):
        input_schema = {}
    return ToolSpec(name=name, description=description, input_schema=input_schema)


def _extract_texts(result: dict) -> str:
    """提取 tools/call 结果的 text 内容；非 text 项以占位说明呈现。"""
    content = result.get("content", [])
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif isinstance(item, dict):
            kind = item.get("type", "未知")
            parts.append(f"（MCP 返回了 {kind} 类型内容，无法在终端展示）")
    return "\n".join(parts)
