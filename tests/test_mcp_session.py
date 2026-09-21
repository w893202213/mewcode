"""MCP 会话层测试：握手版本校验、游标翻页、content 提取、isError、桥接超时。

假 Transport 用两个队列驱动；会话的同步方法（start/list_tools/
call_tool_sync）在主线程调用，服务端行为（serve 协程）在独立线程的
事件循环里跑——同步桥接与专属循环线程因此走真实路径。
"""

import asyncio
import queue
import threading

import pytest

import mewcode.mcp.session as session_module
from mewcode.mcp.config import McpServerConfig
from mewcode.mcp.protocol import McpServerError, Transport
from mewcode.mcp.session import McpSession

# 一份 stdio 配置（假 Transport 下 transport 字段仅作占位）
CFG = McpServerConfig(name="demo", transport="stdio", command="python")


class FakeTransport(Transport):
    """线程安全队列驱动的假传输（跨事件循环使用，故不用 asyncio.Queue）。

    read_message 以 0.1 秒轮询而非永久阻塞——避免 executor 线程卡在
    队列上导致解释器无法退出（非 daemon 线程）。
    """

    def __init__(self) -> None:
        self._outbox: queue.Queue[dict] = queue.Queue()
        self._inbox: queue.Queue[dict] = queue.Queue()
        self._closed = False
        self.closed = False

    async def connect(self) -> None:
        pass

    async def read_message(self) -> dict:
        while not self._closed:
            try:
                return await asyncio.to_thread(self._inbox.get, timeout=0.1)
            except queue.Empty:
                continue
        raise McpServerError("连接已关闭")

    async def write_message(self, msg: dict) -> None:
        await asyncio.to_thread(self._outbox.put, msg)

    async def close(self) -> None:
        self._closed = True
        self.closed = True

    # 服务端侧（测试协程）用的收发原语
    async def serve_send(self, msg: dict) -> None:
        await asyncio.to_thread(self._inbox.put, msg)

    async def serve_recv(self) -> dict:
        return await asyncio.to_thread(self._outbox.get, timeout=2.0)


def _call_with_server(session_fn, serve_coro) -> dict:
    """并发执行：serve 协程跑在独立线程的事件循环，会话方法跑在主线程。

    返回 {"result": ...} 或 {"error": ...}（会话方法抛出的异常）。
    """
    box: dict = {}

    def serve_target() -> None:
        asyncio.run(serve_coro())

    thread = threading.Thread(target=serve_target)
    thread.start()
    try:
        try:
            box["result"] = session_fn()
        except Exception as e:  # 会话方法的任何异常都捕获回测试断言
            box["error"] = e
    finally:
        thread.join(timeout=30.0)
    return box


async def _handshake_serve(t: FakeTransport) -> None:
    """服务端侧：完成一次 initialize 握手。"""
    req = await t.serve_recv()
    assert req["method"] == "initialize"
    await t.serve_send(
        {"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "2025-03-26"}}
    )
    notif = await t.serve_recv()  # 消费 initialized 通知
    assert notif["method"] == "notifications/initialized"


def _make_session(t: FakeTransport, monkeypatch) -> McpSession:
    """建会话并注入假 Transport（替换 create_transport 工厂）。"""
    monkeypatch.setattr(session_module, "create_transport", lambda config: t)
    return McpSession(CFG)


def _started_session(t: FakeTransport, monkeypatch) -> McpSession:
    """建会话并完成握手。"""
    session = _make_session(t, monkeypatch)
    box = _call_with_server(lambda: session.start(), lambda: _handshake_serve(t))
    assert "error" not in box, box.get("error")
    return session


# ---------------------------------------------------------------------------
# 握手
# ---------------------------------------------------------------------------


def test_start_handshake_success(monkeypatch):
    t = FakeTransport()
    session = _make_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        assert req["method"] == "initialize"
        assert req["params"]["protocolVersion"] == "2025-03-26"
        assert req["params"]["capabilities"] == {"tools": {}}
        await t.serve_send(
            {"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "2025-03-26"}}
        )
        notif = await t.serve_recv()
        assert notif["method"] == "notifications/initialized"
        assert "id" not in notif

    box = _call_with_server(lambda: session.start(), serve)
    assert "error" not in box, box.get("error")
    session.close_sync()


def test_start_unsupported_version(monkeypatch):
    t = FakeTransport()
    session = _make_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        await t.serve_send(
            {"jsonrpc": "2.0", "id": req["id"], "result": {"protocolVersion": "1999-01-01"}}
        )

    box = _call_with_server(lambda: session.start(), serve)
    assert isinstance(box["error"], McpServerError)
    assert "协议版本不支持" in str(box["error"])
    session.close_sync()


def test_start_initialize_error(monkeypatch):
    """initialize 返回 JSON-RPC 错误 → 中文错误上抛。"""
    t = FakeTransport()
    session = _make_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "error": {"code": -32000, "message": "server 拒绝"},
            }
        )

    box = _call_with_server(lambda: session.start(), serve)
    assert isinstance(box["error"], McpServerError)
    assert "server 拒绝" in str(box["error"])
    session.close_sync()


# ---------------------------------------------------------------------------
# 列出工具（游标翻页）
# ---------------------------------------------------------------------------


def test_list_tools_pagination(monkeypatch):
    t = FakeTransport()
    session = _started_session(t, monkeypatch)

    async def serve():
        # 第一页：2 个工具 + nextCursor
        req = await t.serve_recv()
        assert req["params"] == {}
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "tools": [
                        {"name": "a", "description": "工具 a", "inputSchema": {"type": "object"}},
                        {"name": "bad", "description": 123},  # description 非字符串 → 归一为空
                    ],
                    "nextCursor": "page2",
                },
            }
        )
        # 第二页：1 个工具，无 nextCursor（末页）
        req = await t.serve_recv()
        assert req["params"] == {"cursor": "page2"}
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"tools": [{"name": "b", "inputSchema": {"type": "object"}}]},
            }
        )

    box = _call_with_server(lambda: session.list_tools(), serve)
    tools = box["result"]
    assert [tool.name for tool in tools] == ["a", "bad", "b"]
    assert tools[0].description == "工具 a"
    assert tools[1].description == ""  # 非法 description 归一为空
    session.close_sync()


def test_list_tools_skips_invalid_entries(monkeypatch):
    """name 缺失/非字符串的条目跳过。"""
    t = FakeTransport()
    session = _started_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {"tools": [{"description": "无名字"}, "not-a-dict"]},
            }
        )

    box = _call_with_server(lambda: session.list_tools(), serve)
    assert box["result"] == []
    session.close_sync()


# ---------------------------------------------------------------------------
# 调用工具
# ---------------------------------------------------------------------------


def test_call_tool_text_extraction(monkeypatch):
    """多条 text 按换行拼接；非 text 占位说明。"""
    t = FakeTransport()
    session = _started_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        assert req["method"] == "tools/call"
        assert req["params"] == {"name": "echo", "arguments": {"x": 1}}
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "content": [
                        {"type": "text", "text": "第一行"},
                        {"type": "image", "data": "base64..."},
                        {"type": "text", "text": "第三行"},
                    ]
                },
            }
        )

    box = _call_with_server(lambda: session.call_tool_sync("echo", {"x": 1}), serve)
    text = box["result"]
    assert "第一行" in text and "第三行" in text
    assert "image 类型内容" in text
    session.close_sync()


def test_call_tool_empty_content(monkeypatch):
    t = FakeTransport()
    session = _started_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        await t.serve_send({"jsonrpc": "2.0", "id": req["id"], "result": {"content": []}})

    box = _call_with_server(lambda: session.call_tool_sync("echo", {}), serve)
    assert box["result"] == ""
    session.close_sync()


def test_call_tool_is_error(monkeypatch):
    t = FakeTransport()
    session = _started_session(t, monkeypatch)

    async def serve():
        req = await t.serve_recv()
        await t.serve_send(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "result": {
                    "content": [{"type": "text", "text": "参数非法"}],
                    "isError": True,
                },
            }
        )

    box = _call_with_server(lambda: session.call_tool_sync("fail", {}), serve)
    assert isinstance(box["error"], McpServerError)
    assert "参数非法" in str(box["error"])
    session.close_sync()


def test_call_tool_sync_timeout(monkeypatch):
    """服务端不回包 → 桥接超时，中文消息先于 run_tool 兜底。"""
    monkeypatch.setattr(session_module, "CALL_TIMEOUT", 0.5)
    t = FakeTransport()
    session = _started_session(t, monkeypatch)
    box = _call_with_server(lambda: session.call_tool_sync("echo", {}), lambda: _never(t))
    assert isinstance(box["error"], McpServerError)
    assert "超时" in str(box["error"])
    session.close_sync()


async def _never(t: FakeTransport) -> None:
    """服务端侧：消费请求后永不回包。"""
    await t.serve_recv()
    await asyncio.sleep(5.0)


def test_call_after_close_fails():
    session = McpSession(CFG)
    session.close_sync()
    with pytest.raises(McpServerError, match="已关闭"):
        session.call_tool_sync("echo", {})
    with pytest.raises(McpServerError, match="已关闭"):
        session.list_tools()


def test_close_sync_idempotent():
    session = McpSession(CFG)
    session.close_sync()
    session.close_sync()  # 不抛异常即可
    assert not session._thread.is_alive()  # 线程已 join 退出
