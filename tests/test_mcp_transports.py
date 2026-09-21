"""MCP 传输层测试：stdio 真实子进程收发；HTTP 单 JSON / SSE / 会话头。

HTTP 端点用 stdlib http.server 起线程服务，覆盖：单 JSON 响应、
SSE 响应（含多事件）、SSE 挂起流的宽限收尾、非 200、Session-Id
回带与配置 headers 透传。
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.protocol import McpServerError
from mewcode.mcp.transports import HttpTransport, StdioTransport

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _stdio_config() -> McpServerConfig:
    """指向 mock MCP Server 的 stdio 配置。"""
    return McpServerConfig(
        name="demo",
        transport="stdio",
        command=sys.executable,
        args=[str(SCRIPTS / "mock_mcp_server.py")],
    )


# ---------------------------------------------------------------------------
# stdio
# ---------------------------------------------------------------------------


def test_stdio_roundtrip():
    async def go():
        transport = StdioTransport(_stdio_config())
        await transport.connect()
        await transport.write_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-03-26"},
            }
        )
        msg = await transport.read_message()
        assert msg["id"] == 1
        assert msg["result"]["protocolVersion"] == "2025-03-26"
        proc = transport._proc
        await transport.close()
        assert proc.returncode is not None  # 子进程已终止

    import asyncio

    asyncio.run(go())


def test_stdio_eof_raises():
    async def go():
        transport = StdioTransport(_stdio_config())
        await transport.connect()
        proc = transport._proc
        proc.terminate()  # 杀进程：stdout 关闭
        with pytest.raises(McpServerError, match="进程已退出"):
            await transport.read_message()
        await transport.close()  # 幂等：进程已退出不再 terminate

    import asyncio

    asyncio.run(go())


def test_stdio_command_missing():
    async def go():
        transport = StdioTransport(
            McpServerConfig(name="bad", transport="stdio", command="/no/such/cmd")
        )
        with pytest.raises(McpServerError, match="启动失败"):
            await transport.connect()

    import asyncio

    asyncio.run(go())


# ---------------------------------------------------------------------------
# HTTP：stdlib http.server 端点
# ---------------------------------------------------------------------------

# 服务端记录收到的请求（线程共享）
RECEIVED: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    """按路径路由的 mock Streamable HTTP 端点。"""

    def log_message(self, *args):  # 静音访问日志
        pass

    def _record(self):
        RECEIVED.append(
            {
                "path": self.path,
                "session_id": self.headers.get("Mcp-Session-Id"),
                "authorization": self.headers.get("Authorization"),
                "body": self.rfile.read(int(self.headers.get("Content-Length", 0))),
            }
        )

    def do_POST(self):
        self._record()
        if self.path == "/json":
            self._reply(
                200,
                {"Content-Type": "application/json"},
                json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"mode": "json"}}),
            )
        elif self.path == "/sse":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            data = json.dumps({"jsonrpc": "2.0", "id": 8, "result": {"mode": "sse"}})
            self.wfile.write(f"event: message\ndata: {data}\n\n".encode())
            self.wfile.flush()
            self.close_connection = True
        elif self.path == "/sse-multi":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            d1 = json.dumps({"jsonrpc": "2.0", "method": "notifications/n1"})
            d2 = json.dumps({"jsonrpc": "2.0", "id": 9, "result": {"mode": "multi"}})
            self.wfile.write(f"event: message\ndata: {d1}\n\nevent: message\ndata: {d2}\n\n".encode())
            self.wfile.flush()
            self.close_connection = True
        elif self.path == "/sse-hold":
            # 发一条消息事件后保持流打开（测试宽限收尾）
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            data = json.dumps({"jsonrpc": "2.0", "id": 10, "result": {"mode": "hold"}})
            self.wfile.write(f"event: message\ndata: {data}\n\n".encode())
            self.wfile.flush()
            import time

            time.sleep(10)  # 长时间挂起；客户端宽限期到会主动收尾
        elif self.path == "/session":
            self._reply(
                200,
                {
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": "sess-abc",
                },
                json.dumps({"jsonrpc": "2.0", "id": 11, "result": {}}),
            )
        else:
            self._reply(500, {"Content-Type": "text/plain"}, "boom")

    def _reply(self, code: int, headers: dict, body: str):
        self.send_response(code)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body.encode())))
        self.end_headers()
        self.wfile.write(body.encode())


@pytest.fixture(autouse=True)
def _clear_received():
    """每个测试前清空服务端请求记录。"""
    RECEIVED.clear()


@pytest.fixture(scope="module")
def http_server():
    """起一个 mock Streamable HTTP 服务（模块级共享，返回 base URL）。"""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _http_config(base: str, path: str, headers: dict | None = None) -> McpServerConfig:
    return McpServerConfig(
        name="remote",
        transport="http",
        url=f"{base}{path}",
        headers=headers or {},
    )


def _roundtrip(config: McpServerConfig, request: dict) -> dict:
    """POST 一条请求并取回下一条响应消息。"""
    import asyncio

    async def go():
        transport = HttpTransport(config)
        await transport.connect()
        await transport.write_message(request)
        msg = await transport.read_message()
        await transport.close()
        return msg

    return asyncio.run(go())


def test_http_single_json(http_server):
    msg = _roundtrip(
        _http_config(http_server, "/json"),
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
    )
    assert msg["id"] == 7
    assert msg["result"] == {"mode": "json"}


def test_http_sse(http_server):
    msg = _roundtrip(
        _http_config(http_server, "/sse"),
        {"jsonrpc": "2.0", "id": 8, "method": "ping"},
    )
    assert msg["id"] == 8
    assert msg["result"] == {"mode": "sse"}


def test_http_sse_multi_event(http_server):
    """一个 SSE 流里的多条事件按序入队。"""
    import asyncio

    async def go():
        transport = HttpTransport(_http_config(http_server, "/sse-multi"))
        await transport.connect()
        await transport.write_message({"jsonrpc": "2.0", "id": 9, "method": "ping"})
        first = await transport.read_message()
        second = await transport.read_message()
        await transport.close()
        return first, second

    first, second = asyncio.run(go())
    assert first == {"jsonrpc": "2.0", "method": "notifications/n1"}
    assert second["id"] == 9


def test_http_sse_hold_stream_grace(monkeypatch, http_server):
    """服务端发完响应后挂起流：客户端宽限期到主动收尾，不死锁。"""
    import mewcode.mcp.transports as transports_module

    monkeypatch.setattr(transports_module, "_SSE_GRACE", 0.3)
    msg = _roundtrip(
        _http_config(http_server, "/sse-hold"),
        {"jsonrpc": "2.0", "id": 10, "method": "ping"},
    )
    assert msg["id"] == 10
    assert msg["result"] == {"mode": "hold"}


def test_http_non_200(http_server):
    with pytest.raises(McpServerError, match="500"):
        _roundtrip(
            _http_config(http_server, "/other"),
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )


def test_http_session_id_echoed(http_server):
    """服务端返回 Mcp-Session-Id 后，后续请求回带该头。"""
    import asyncio

    async def go():
        transport = HttpTransport(_http_config(http_server, "/session"))
        await transport.connect()
        await transport.write_message({"jsonrpc": "2.0", "id": 11, "method": "ping"})
        await transport.read_message()
        await transport.write_message({"jsonrpc": "2.0", "id": 12, "method": "ping"})
        await transport.read_message()
        await transport.close()

    asyncio.run(go())
    assert RECEIVED[0]["session_id"] is None
    assert RECEIVED[1]["session_id"] == "sess-abc"


def test_http_config_headers_sent(http_server):
    _roundtrip(
        _http_config(http_server, "/json", headers={"Authorization": "Bearer xyz"}),
        {"jsonrpc": "2.0", "id": 13, "method": "ping"},
    )
    assert RECEIVED[0]["authorization"] == "Bearer xyz"
