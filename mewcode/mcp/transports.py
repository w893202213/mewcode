"""MCP 传输层：stdio 子进程管道与 Streamable HTTP 两种实现。

- StdioTransport：换行分隔 JSON（MCP stdio 规范：消息单行、无内嵌换行）
- HttpTransport：POST 发请求；响应兼容两种形态——单 JSON 体与
  text/event-stream（SSE，逐 `event: message` + `data:` 解析）；服务端
  返回的 Mcp-Session-Id 存下并随后续请求回带。

两个实现的 read_message 都向 RpcClient 的读循环提供「一条已解析的
JSON 消息」；坏 JSON 一律抛 McpServerError（协议层据此标记连接损坏）。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.protocol import McpServerError, Transport

# SSE 流在最后一条消息事件后保持打开的宽限秒数：超时即视为服务端
# 不会再发更多事件，主动收尾（防止服务端挂起流导致写锁死锁）
_SSE_GRACE = 3.0

# HTTP 读写超时（秒）；调用级 25 秒桥接超时仍由会话层兜底
_HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class StdioTransport(Transport):
    """stdio 传输：启动子进程，换行分隔 JSON 消息。"""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._proc: asyncio.subprocess.Process | None = None

    async def connect(self) -> None:
        import os

        env = dict(os.environ)
        env.update(self._config.env)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                self._config.command,
                *(self._config.args),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=None,  # 继承父进程 stderr，便于排查 Server 自身报错
                env=env,
            )
        except OSError as e:
            raise McpServerError(f"MCP Server 子进程启动失败: {e}") from e

    async def read_message(self) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        line = await self._proc.stdout.readline()
        if not line:  # EOF：Server 进程已退出
            raise McpServerError("MCP Server 进程已退出（stdout 关闭）")
        try:
            msg = json.loads(line.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise McpServerError(f"收到无法解析的消息: {e}") from e
        if not isinstance(msg, dict):
            raise McpServerError("收到无法解析的消息: 不是 JSON 对象")
        return msg

    async def write_message(self, msg: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        data = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as e:
            raise McpServerError(f"MCP Server 管道已断: {e}") from e

    async def close(self) -> None:
        """终止子进程并回收；进程已退出则跳过（幂等）。"""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), 3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


class HttpTransport(Transport):
    """Streamable HTTP 传输：POST 请求，兼容单 JSON 与 SSE 响应。"""

    def __init__(self, config: McpServerConfig) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._session_id: str | None = None
        self._inbox: asyncio.Queue[dict] = asyncio.Queue()

    async def connect(self) -> None:
        # 在所属事件循环内创建客户端（httpx 异步客户端绑定创建时的循环）
        self._client = httpx.AsyncClient(timeout=_HTTP_TIMEOUT)

    def _headers(self) -> dict[str, str]:
        """合并配置 headers + 固定头 + 已存的会话 ID。"""
        headers = dict(self._config.headers)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json, text/event-stream")
        if self._session_id is not None:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    async def write_message(self, msg: dict) -> None:
        assert self._client is not None
        try:
            # client.stream：响应体惰性读取——SSE 挂起流由 _read_sse 的
            # 宽限机制主动收尾，不能让 httpx 在请求内整读响应体
            async with self._client.stream(
                "POST", self._config.url, json=msg, headers=self._headers()
            ) as response:
                session_id = response.headers.get("mcp-session-id")
                if session_id:
                    self._session_id = session_id
                if response.status_code != 200:
                    raise McpServerError(
                        f"HTTP 状态码 {response.status_code}，MCP Server 连接失败"
                    )
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" in content_type:
                    await self._read_sse(response)
                elif "application/json" in content_type:
                    await response.aread()  # stream 模式下先读体再解析
                    try:
                        msg_in = response.json()
                    except ValueError as e:
                        raise McpServerError(f"响应不是合法 JSON: {e}") from e
                    if not isinstance(msg_in, dict):
                        raise McpServerError("响应不是 JSON 对象")
                    await self._inbox.put(msg_in)
                else:
                    raise McpServerError(f"不支持的响应类型: {content_type}")
        except httpx.HTTPError as e:
            raise McpServerError(f"HTTP 请求失败: {e}") from e

    async def _read_sse(self, response: httpx.Response) -> None:
        """逐行解析 SSE：按空行切事件，`event: message` 的 data 解析为 JSON 入队。

        最后一条消息事件后等待 _SSE_GRACE 秒，服务端不关流也不再发
        事件即主动收尾（防止流挂起占住写锁）。
        """
        iterator = response.aiter_lines().__aiter__()
        deadline: float | None = None
        event_name = ""
        data_lines: list[str] = []
        loop = asyncio.get_running_loop()
        try:
            while True:
                if deadline is None:
                    line = await iterator.__anext__()
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    line = await asyncio.wait_for(iterator.__anext__(), remaining)
                if line == "":
                    # 事件结束：只处理 message 事件（data 可能是多行拼接）
                    if event_name in ("", "message") and data_lines:
                        data = "\n".join(data_lines)
                        try:
                            msg = json.loads(data)
                        except json.JSONDecodeError as e:
                            raise McpServerError(f"SSE 事件数据不是合法 JSON: {e}") from e
                        if not isinstance(msg, dict):
                            raise McpServerError("SSE 事件数据不是 JSON 对象")
                        await self._inbox.put(msg)
                        deadline = loop.time() + _SSE_GRACE
                    event_name = ""
                    data_lines = []
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())
        except StopAsyncIteration:
            pass  # 服务端正常关流
        except asyncio.TimeoutError:
            pass  # 宽限期到：服务端挂起流，主动收尾

    async def read_message(self) -> dict:
        return await self._inbox.get()

    async def close(self) -> None:
        """关闭 HTTP 客户端；幂等。"""
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()


def create_transport(config: McpServerConfig) -> Transport:
    """按配置的 transport 字段选择实现。"""
    if config.transport == "stdio":
        return StdioTransport(config)
    return HttpTransport(config)
