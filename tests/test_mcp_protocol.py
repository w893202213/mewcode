"""MCP 协议层测试：RpcClient 的 id 配对、三态处理、损坏与超时。

假 Transport 用两个 asyncio.Queue 驱动：write_message 进 outbox
（测试侧读走并应答），read_message 从 inbox 取（测试侧喂入）。
"""

import asyncio

import pytest

from mewcode.mcp.protocol import ERROR_METHOD_NOT_FOUND, McpServerError, RpcClient, Transport


class FakeTransport(Transport):
    """队列驱动的假传输：测试控制收发两端。"""

    def __init__(self) -> None:
        self.outbox: asyncio.Queue[dict] = asyncio.Queue()
        self.inbox: asyncio.Queue[dict] = asyncio.Queue()
        self.fail_event = asyncio.Event()  # 置位即模拟读侧断开
        self.closed = False

    async def connect(self) -> None:
        pass

    async def read_message(self) -> dict:
        get_task = asyncio.create_task(self.inbox.get())
        fail_task = asyncio.create_task(self.fail_event.wait())
        done, pending = await asyncio.wait(
            {get_task, fail_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        if fail_task in done:
            raise McpServerError("管道已断")
        return get_task.result()

    async def write_message(self, msg: dict) -> None:
        await self.outbox.put(msg)

    async def close(self) -> None:
        self.closed = True


async def _started_client(transport: FakeTransport) -> RpcClient:
    """启动一个挂好读循环的客户端。"""
    client = RpcClient(transport)
    await client.start()
    return client


async def _serve_one(transport: FakeTransport, result: dict | None = None) -> dict:
    """读走一条 outbox 请求，可选的按原 id 回 result，返回请求消息。"""
    req = await transport.outbox.get()
    if result is not None:
        await transport.inbox.put(
            {"jsonrpc": "2.0", "id": req["id"], "result": result}
        )
    return req


def test_request_roundtrip():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        req_task = asyncio.create_task(client.request("echo", {"x": 1}, 1.0))
        req = await _serve_one(t, result={"ok": True})
        assert req["jsonrpc"] == "2.0"
        assert req["method"] == "echo"
        assert req["params"] == {"x": 1}
        assert (await req_task) == {"ok": True}
        await client.close()

    asyncio.run(go())


def test_out_of_order_responses_pair_by_id():
    """乱序响应按 id 配对，各归各位。"""
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        first = asyncio.create_task(client.request("first", {}, 1.0))
        second = asyncio.create_task(client.request("second", {}, 1.0))
        req1 = await t.outbox.get()
        req2 = await t.outbox.get()
        # 先回第二个请求
        await t.inbox.put({"jsonrpc": "2.0", "id": req2["id"], "result": "b"})
        assert (await second) == "b"
        await t.inbox.put({"jsonrpc": "2.0", "id": req1["id"], "result": "a"})
        assert (await first) == "a"
        await client.close()

    asyncio.run(go())


def test_error_response_raises():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        task = asyncio.create_task(client.request("bad", {}, 1.0))
        req = await t.outbox.get()
        await t.inbox.put(
            {
                "jsonrpc": "2.0",
                "id": req["id"],
                "error": {"code": -32602, "message": "参数非法"},
            }
        )
        with pytest.raises(McpServerError) as exc_info:
            await task
        assert "参数非法" in str(exc_info.value)
        assert exc_info.value.code == -32602
        await client.close()

    asyncio.run(go())


def test_incoming_notification_ignored():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        # 通知（有 method 无 id）不应有任何回包，也不影响 pending
        await t.inbox.put({"jsonrpc": "2.0", "method": "notifications/hello"})
        task = asyncio.create_task(client.request("echo", {}, 1.0))
        req = await t.outbox.get()  # 第一条 outbox 必须是 echo 请求，通知不产生回包
        assert req["method"] == "echo"
        await t.inbox.put({"jsonrpc": "2.0", "id": req["id"], "result": {}})
        assert (await task) == {}
        assert t.outbox.empty()  # 除请求外无任何多余消息
        await client.close()

    asyncio.run(go())


def test_server_request_gets_method_not_found():
    """Server 发来的请求（有 id 有 method）回 -32601。"""
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        await t.inbox.put({"jsonrpc": "2.0", "id": 99, "method": "sampling/createMessage"})
        reply = await asyncio.wait_for(t.outbox.get(), 1.0)
        assert reply["id"] == 99
        assert reply["error"]["code"] == ERROR_METHOD_NOT_FOUND
        assert "sampling/createMessage" in reply["error"]["message"]
        await client.close()

    asyncio.run(go())


def test_transport_failure_breaks_connection():
    """读侧断开：pending 报「连接已断开」，连接标记损坏，后续调用直接失败。"""
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        task = asyncio.create_task(client.request("echo", {}, 5.0))
        await t.outbox.get()
        t.fail_event.set()  # 模拟管道断：读循环立即抛 McpServerError
        with pytest.raises(McpServerError, match="连接已断开"):
            await task
        with pytest.raises(McpServerError, match="连接已损坏"):
            await client.request("echo", {}, 1.0)
        await client.close()

    asyncio.run(go())


def test_request_timeout():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        task = asyncio.create_task(client.request("slow", {}, 0.05))
        await t.outbox.get()  # 不回包
        with pytest.raises(McpServerError, match="超时"):
            await task
        # 超时后连接仍可用：新请求正常配对
        ok_task = asyncio.create_task(client.request("ok", {}, 1.0))
        await _serve_one(t, result={"fine": 1})
        assert (await ok_task) == {"fine": 1}
        await client.close()

    asyncio.run(go())


def test_close_is_idempotent():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        await client.close()
        await client.close()
        assert t.closed

    asyncio.run(go())


def test_notify_sends_no_id():
    async def go():
        t = FakeTransport()
        client = await _started_client(t)
        await client.notify("notifications/initialized", {})
        msg = await t.outbox.get()
        assert msg["method"] == "notifications/initialized"
        assert "id" not in msg
        await client.close()

    asyncio.run(go())
