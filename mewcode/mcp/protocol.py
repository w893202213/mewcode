"""JSON-RPC 2.0 协议层：消息收发、请求按 id 异步配对、三态处理。

传输无关：RpcClient 只依赖 Transport 抽象（connect/read/write/close），
每个连接一个实例。请求 id 自增；响应/错误按 id 配对唤醒；通知忽略；
Server 发来的请求回 method-not-found（采样等能力不在范围）。

读循环遇到坏 JSON 一条即把连接标记为损坏：所有等待中的请求报中文
「连接已损坏」错误，后续请求同样直接失败（fail-closed，不做半修复）。
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# JSON-RPC 标准错误码：方法未找到
ERROR_METHOD_NOT_FOUND = -32601


class McpServerError(Exception):
    """MCP 层错误基类，消息一律中文、可读。

    code/data 携带远端返回的 JSON-RPC 错误详情（可为 None）。
    """

    def __init__(self, message: str, code: int | None = None, data=None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class Transport(ABC):
    """传输抽象：一条连接上的读写与生命周期。"""

    @abstractmethod
    async def connect(self) -> None:
        """建立连接（启动子进程/建 HTTP 客户端）。"""

    @abstractmethod
    async def read_message(self) -> dict:
        """读入一条 JSON 消息；连接结束抛 McpServerError。"""

    @abstractmethod
    async def write_message(self, msg: dict) -> None:
        """写出一条 JSON 消息。"""

    @abstractmethod
    async def close(self) -> None:
        """关闭连接（终止子进程/关 HTTP 客户端），幂等。"""


class RpcClient:
    """JSON-RPC 客户端：请求/通知发送与 id 配对。"""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task: asyncio.Task | None = None
        self._broken = False

    async def start(self) -> None:
        """建立连接并启动读循环。"""
        await self._transport.connect()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def request(self, method: str, params: dict | None, timeout: float) -> dict:
        """发送请求并等待同 id 响应，返回 result；错误/超时抛 McpServerError。"""
        if self._broken:
            raise McpServerError("MCP 连接已损坏，无法继续调用")
        self._next_id += 1
        msg_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        try:
            await self._send(
                {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}
            )
            try:
                return await asyncio.wait_for(future, timeout)
            except asyncio.TimeoutError:
                raise McpServerError(f"MCP 调用超时（{timeout:g} 秒）") from None
        finally:
            self._pending.pop(msg_id, None)

    async def notify(self, method: str, params: dict | None) -> None:
        """发送通知（无 id），不等响应。"""
        await self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def close(self) -> None:
        """停止读循环并关闭连接；幂等。"""
        self._broken = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, McpServerError):
                pass
            self._reader_task = None
        self._fail_pending("MCP 连接已关闭")
        await self._transport.close()

    async def _send(self, msg: dict) -> None:
        """写出一条消息（并发请求共享传输，写侧加锁）。"""
        async with self._write_lock:
            await self._transport.write_message(msg)

    def _fail_pending(self, message: str) -> None:
        """让所有等待中的请求以指定错误失败。"""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(McpServerError(message))

    async def _read_loop(self) -> None:
        """读循环：三态处理 + 坏 JSON 标记损坏。"""
        while True:
            try:
                msg = await self._transport.read_message()
            except asyncio.CancelledError:
                raise
            except McpServerError:
                # 连接结束：等待中的请求失败，连接标记损坏
                self._broken = True
                self._fail_pending("MCP 连接已断开")
                return
            self._dispatch(msg)

    def _dispatch(self, msg: dict) -> None:
        """按消息形态分发：响应配对 / 通知忽略 / Server 请求回错误。"""
        if "method" in msg and "id" in msg:
            # Server 发来的请求：回 method-not-found（采样等能力不在范围），不静默丢弃
            asyncio.get_running_loop().create_task(
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {
                            "code": ERROR_METHOD_NOT_FOUND,
                            "message": f"方法未找到: {msg.get('method')}",
                        },
                    }
                )
            )
            return
        if "id" in msg:
            if "error" in msg:
                error = msg["error"]
                message = (
                    error.get("message", "未知错误") if isinstance(error, dict) else str(error)
                )
                future = self._pending.get(msg["id"])
                if future is not None and not future.done():
                    future.set_exception(
                        McpServerError(
                            f"MCP Server 返回错误: {message}",
                            code=error.get("code") if isinstance(error, dict) else None,
                        )
                    )
            elif "result" in msg:
                future = self._pending.get(msg["id"])
                if future is not None and not future.done():
                    future.set_result(msg["result"])
            # 既无 error 也无 result 的 id 消息不合法，忽略
            return
        # 通知（无 id 无 method）与无 id 消息：忽略
