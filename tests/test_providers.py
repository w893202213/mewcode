"""Provider 层测试：本地 mock SSE 服务，验证两家后端端到端行为。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mewcode.config import ProviderConfig
from mewcode.provider import create_provider
from mewcode.provider.base import Done, ProviderError, SystemPrompt, TextDelta
from mewcode.session import ChatSession, Message

# 不关心系统提示的调用点统一传入空 SystemPrompt（请求体省略 system 字段）
_NO_SYSTEM = SystemPrompt("", "")

# mock 服务返回的 Anthropic SSE 事件序列（message_start 用量 + 思考块 + 文本块）
ANTHROPIC_EVENTS = [
    '{"type":"message_start","message":{"usage":{"input_tokens":100,"output_tokens":1,"cache_creation_input_tokens":80,"cache_read_input_tokens":0}}}',
    '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"让我想想","signature":"sig-1"}}',
    '{"type":"content_block_stop","index":0}',
    '{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"答案是"}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"42"}}',
    '{"type":"message_stop"}',
]

# mock 服务返回的 OpenAI SSE 事件序列
OPENAI_CHUNKS = [
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"你好"},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"！"},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
    'data: [DONE]',
]


class MockHandler(BaseHTTPRequestHandler):
    """记录收到的请求体；按 model 名触发不同响应场景。"""

    received: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        MockHandler.received.append({"path": self.path, "body": body})
        model = body.get("model", "")
        if self.path.endswith("/v1/messages"):
            if model == "error-model":
                # 流中途 error 事件
                self._send_sse(
                    ['{"type":"error","error":{"type":"overloaded_error","message":"服务过载"}}']
                )
            else:
                self._send_sse(ANTHROPIC_EVENTS)
        elif self.path.endswith("/v1/chat/completions"):
            if model == "unauthorized-model":
                self._send_json(401, {"error": {"message": "invalid api key"}})
            else:
                self._send_sse(OPENAI_CHUNKS)
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def _send_sse(self, events: list[str]):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in events:
            if not e.startswith("data:"):
                e = f"data: {e}"
            self.wfile.write(f"{e}\n\n".encode())

    def _send_json(self, status: int, obj: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def log_message(self, *args):  # 静音访问日志
        pass


@pytest.fixture(scope="module")
def mock_server():
    server = HTTPServer(("127.0.0.1", 0), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


@pytest.fixture(autouse=True)
def _clear_received():
    MockHandler.received.clear()


def _cfg(protocol: str, model: str = "m", thinking: bool = False, base_url: str = "") -> ProviderConfig:
    return ProviderConfig(
        name="t", protocol=protocol, model=model, base_url=base_url, api_key="k", thinking=thinking
    )


def test_anthropic_stream_events(mock_server):
    provider = create_provider(_cfg("anthropic", base_url=mock_server))
    events = list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    assert [type(e).__name__ for e in events] == ["ThinkingDelta", "TextDelta", "TextDelta", "Done"]
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "答案是42"
    done = events[-1]
    assert isinstance(done, Done)
    assert done.thinking_raw == [
        {"type": "thinking", "thinking": "让我想想", "signature": "sig-1"}
    ]


def test_anthropic_request_body_with_thinking(mock_server):
    provider = create_provider(_cfg("anthropic", thinking=True, base_url=mock_server))
    list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    body = MockHandler.received[-1]["body"]
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 4096}
    assert body["stream"] is True
    assert body["messages"][0] == {"role": "user", "content": "问"}


def test_anthropic_request_body_without_thinking(mock_server):
    provider = create_provider(_cfg("anthropic", base_url=mock_server))
    list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    assert "thinking" not in MockHandler.received[-1]["body"]


def test_thinking_blocks_roundtrip(mock_server):
    """第一轮的思考块必须原样出现在第二轮请求的 assistant 消息里。"""
    provider = create_provider(_cfg("anthropic", base_url=mock_server))
    session = ChatSession()
    session.add_user("你好")
    events = list(provider.stream(session.messages, [], _NO_SYSTEM))
    done = next(e for e in events if isinstance(e, Done))
    session.add_assistant(text="答案是42", thinking="让我想想", thinking_raw=done.thinking_raw)
    session.add_user("继续")
    list(provider.stream(session.messages, [], _NO_SYSTEM))
    assistant_msg = MockHandler.received[-1]["body"]["messages"][1]
    assert assistant_msg["content"] == [
        {"type": "thinking", "thinking": "让我想想", "signature": "sig-1"},
        {"type": "text", "text": "答案是42"},
    ]


def test_openai_stream_events(mock_server):
    provider = create_provider(_cfg("openai", base_url=mock_server))
    events = list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert text == "你好！"
    assert isinstance(events[-1], Done)


def test_openai_request_body_thinking_passthrough(mock_server):
    provider = create_provider(_cfg("openai", thinking=True, base_url=mock_server))
    list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    body = MockHandler.received[-1]["body"]
    assert body["thinking"] is True
    assert body["messages"] == [{"role": "user", "content": "问"}]


def test_anthropic_midstream_error(mock_server):
    provider = create_provider(_cfg("anthropic", model="error-model", base_url=mock_server))
    with pytest.raises(ProviderError, match="服务过载"):
        list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))


def test_openai_http_401(mock_server):
    provider = create_provider(_cfg("openai", model="unauthorized-model", base_url=mock_server))
    with pytest.raises(ProviderError, match="鉴权失败"):
        list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))


def test_openai_request_body_has_stream_options(mock_server):
    """include_usage 已开启：请求体带 stream_options（F8 token 用量）。"""
    provider = create_provider(_cfg("openai", base_url=mock_server))
    list(provider.stream([Message(role="user", text="问")], [], _NO_SYSTEM))
    body = MockHandler.received[-1]["body"]
    assert body["stream_options"] == {"include_usage": True}
