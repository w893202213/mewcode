"""本地 mock LLM 服务：模拟 Anthropic 与 OpenAI 的 SSE 流式响应，供 e2e 验证。

用法::

    python scripts/mock_llm.py [端口]     # 默认 8989

- POST /v1/messages          → Anthropic 流（思考块 + 文本块，逐块间隔 0.05s 模拟真实流式）
- POST /v1/chat/completions  → OpenAI 流（逐 chunk）
- model 名为 "error-model"   → 流中途 error 事件
- model 名为 "unauthorized-model" → HTTP 401

收到的请求体会以 JSON 行追加写入 ./mock_requests.log，供检查 thinking 透传等字段。
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# Anthropic 流（仅文本块，请求未带 thinking 参数时返回，用于验证 thinking=false 无思考区）
ANTHROPIC_TEXT_ONLY_EVENTS = [
    '{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"**你好！**\\n\\n"}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"我没有思考区。\\n"}}',
    '{"type":"message_stop"}',
]

# Anthropic 流：思考 + 文本（模拟 extended thinking 的增量流）
ANTHROPIC_EVENTS = [
    '{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"让我想想","signature":""}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"这个问题","signature":""}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"的答案…","signature":"mock-sig"}}',
    '{"type":"content_block_stop","index":0}',
    '{"type":"content_block_start","index":1,"content_block":{"type":"text","text":""}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"**你好！**\\n\\n"}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"我是 mock 服务，这是流式回复。\\n\\n"}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"| 列A | 列B |\\n| --- | --- |\\n| 1 | 2 |\\n\\n"}}',
    '{"type":"content_block_delta","index":1,"delta":{"type":"text_delta","text":"```python\\nprint(\\"hello mewcode\\")\\n```"}}',
    '{"type":"message_stop"}',
]

# Anthropic 工具调用流：read_file 调用（参数 JSON 分 4 片流式下发）
ANTHROPIC_TOOL_EVENTS = [
    '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_mock_1","name":"read_file","input":{}}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"file_"}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"path\\": \\"py"}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"project.to"}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"ml\\"}"}}',
    '{"type":"content_block_stop","index":0}',
    '{"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":42}}',
    '{"type":"message_stop"}',
]

# Anthropic 工具调用流：run_command 调用（用于验证命令确认交互）
ANTHROPIC_CMD_EVENTS = [
    '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_cmd_1","name":"run_command","input":{}}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"command\\": \\"echo "}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"hello from "}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"tool\\"}"}}',
    '{"type":"content_block_stop","index":0}',
    '{"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":42}}',
    '{"type":"message_stop"}',
]

# Anthropic 工具调用流：参数拼不出合法 JSON（异常路径）
ANTHROPIC_BADJSON_EVENTS = [
    '{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_bad_1","name":"read_file","input":{}}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\\"file_path\\": "}}',
    '{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"坏掉的"}}',
    '{"type":"content_block_stop","index":0}',
    '{"type":"message_stop"}',
]

# OpenAI 工具调用流：read_file 调用（arguments 分 3 片、带 index）
OPENAI_TOOL_CHUNKS = [
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":null},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_mock_1","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"file_path\\": \\"py"}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"project.toml\\"}"}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
    'data: [DONE]',
]

# OpenAI 工具调用流：run_command 调用
OPENAI_CMD_CHUNKS = [
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":null},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_cmd_1","type":"function","function":{"name":"run_command","arguments":""}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"command\\": \\"echo hello from tool\\"}"}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
    'data: [DONE]',
]

# OpenAI 工具调用流：参数拼不出合法 JSON（异常路径）
OPENAI_BADJSON_CHUNKS = [
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":null},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_bad_1","type":"function","function":{"name":"read_file","arguments":""}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{oops"}}]},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
    'data: [DONE]',
]

OPENAI_CHUNKS = [
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"**你好！** "},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"我是 OpenAI mock，"}},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"流式回复。"}},"finish_reason":null}]}',
    '{"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}',
    'data: [DONE]',
]


class MockHandler(BaseHTTPRequestHandler):
    # 流事件发送间隔（秒），可由 MOCK_DELAY 环境变量调整，默认 0.05
    delay: float = 0.05

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        with open("mock_requests.log", "a", encoding="utf-8") as f:
            f.write(json.dumps({"path": self.path, "body": body}, ensure_ascii=False) + "\n")
        model = body.get("model", "")
        has_tools = bool(body.get("tools"))
        if self.path.endswith("/v1/messages"):
            if model == "error-model":
                self._send_sse(['{"type":"error","error":{"type":"overloaded_error","message":"mock 服务过载"}}'])
            elif model == "unauthorized-model":
                self._send_json(401, {"error": {"message": "mock invalid api key"}})
            elif has_tools and model == "tool-model":
                self._send_sse(ANTHROPIC_TOOL_EVENTS)
            elif has_tools and model == "tool-cmd-model":
                self._send_sse(ANTHROPIC_CMD_EVENTS)
            elif has_tools and model == "tool-badjson-model":
                self._send_sse(ANTHROPIC_BADJSON_EVENTS)
            elif "thinking" in body:
                self._send_sse(ANTHROPIC_EVENTS)
            else:
                # 请求未启用 thinking：只返回文本块（真实 API 行为一致）
                self._send_sse(ANTHROPIC_TEXT_ONLY_EVENTS)
        elif self.path.endswith("/v1/chat/completions"):
            if model == "error-model":
                self._send_sse(['{"error":{"message":"mock 服务出错"}}'])
            elif model == "unauthorized-model":
                self._send_json(401, {"error": {"message": "mock invalid api key"}})
            elif has_tools and model == "tool-model":
                self._send_sse(OPENAI_TOOL_CHUNKS)
            elif has_tools and model == "tool-cmd-model":
                self._send_sse(OPENAI_CMD_CHUNKS)
            elif has_tools and model == "tool-badjson-model":
                self._send_sse(OPENAI_BADJSON_CHUNKS)
            else:
                self._send_sse(OPENAI_CHUNKS)
        else:
            self._send_json(404, {"error": {"message": "not found"}})

    def _send_sse(self, events: list[str], delay: float | None = None):
        # delay 未指定时使用类属性（可经 MOCK_DELAY 环境变量调整）
        if delay is None:
            delay = self.delay
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in events:
            if not e.startswith("data:"):
                e = f"data: {e}"
            self.wfile.write(f"{e}\n\n".encode())
            self.wfile.flush()
            if delay:
                time.sleep(delay)

    def _send_json(self, status: int, obj: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())

    def log_message(self, *args):  # 静音访问日志
        pass


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8989
    # 测试辅助：MOCK_DELAY 环境变量调整流事件间隔（默认 0.05 不变）
    MockHandler.delay = float(os.environ.get("MOCK_DELAY", "0.05"))
    server = HTTPServer(("127.0.0.1", port), MockHandler)
    print(f"mock LLM 服务已启动: http://127.0.0.1:{port}（请求记录写入 mock_requests.log）", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
