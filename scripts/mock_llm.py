"""本地 mock LLM 服务：模拟 Anthropic 与 OpenAI 的 SSE 流式响应，供 e2e 验证。

用法::

    python scripts/mock_llm.py [端口]     # 默认 8989

- POST /v1/messages          → Anthropic 流（思考块 + 文本块，逐块间隔 0.05s 模拟真实流式）
- POST /v1/chat/completions  → OpenAI 流（逐 chunk）
- model 名为 "error-model"   → 流中途 error 事件
- model 名为 "unauthorized-model" → HTTP 401
- ch05 Agent Loop 多轮流：
  - "loop-model"        → 按请求体中已回灌的工具结果数分轮（读文件→跑命令→最终文本）
  - "multi-tool-model"  → 单轮三个工具调用（read_file + grep_code + run_command）
  - "unknown-tool-model"→ 每轮调用不存在的 no_such_tool（触发连续未知工具停止）
  - "never-stop-model"  → 每轮调用 read_file（触发迭代上限）

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


# ---------------------------------------------------------------------------
# ch05: Agent Loop 多轮流构造（事件行用 dict + json.dumps 拼装，避免括号出错）
# ---------------------------------------------------------------------------


def _anthropic_tool_result_count(body: dict) -> int:
    """统计请求体中已回灌的工具结果数（分轮依据）。"""
    count = 0
    for m in body.get("messages", []):
        content = m.get("content")
        if isinstance(content, list):
            count += sum(
                1 for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
            )
    return count


def _openai_tool_result_count(body: dict) -> int:
    return sum(1 for m in body.get("messages", []) if m.get("role") == "tool")


def _anthropic_tool_round(
    tool_use_id: str, name: str, args: dict, text: str | None = None
) -> list[str]:
    """一轮 Anthropic 事件：可选文本块 + 一个完整参数的 tool_use 块 + usage。"""
    events = []
    index = 0
    if text:
        events.append(
            json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
        )
        events.append(
            json.dumps(
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                ensure_ascii=False,
            )
        )
        events.append(json.dumps({"type": "content_block_stop", "index": 0}))
        index = 1
    events.append(
        json.dumps(
            {
                "type": "content_block_start",
                "index": index,
                "content_block": {"type": "tool_use", "id": tool_use_id, "name": name, "input": {}},
            }
        )
    )
    events.append(
        json.dumps(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)},
            },
            ensure_ascii=False,
        )
    )
    events.append(json.dumps({"type": "content_block_stop", "index": index}))
    events.append(
        json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"input_tokens": 100, "output_tokens": 42},
            }
        )
    )
    events.append(json.dumps({"type": "message_stop"}))
    return events


def _anthropic_text_round(text: str) -> list[str]:
    """一轮 Anthropic 纯文本事件（最终回复轮）。"""
    return [
        json.dumps({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        json.dumps(
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
            ensure_ascii=False,
        ),
        json.dumps({"type": "content_block_stop", "index": 0}),
        json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"input_tokens": 100, "output_tokens": 42},
            }
        ),
        json.dumps({"type": "message_stop"}),
    ]


def _anthropic_multi_tool_events() -> list[str]:
    """单轮三个 tool_use 块（read_file + grep_code + run_command）。"""
    calls = [
        ("toolu_m1", "read_file", {"file_path": "pyproject.toml"}),
        ("toolu_m2", "grep_code", {"pattern": "version"}),
        ("toolu_m3", "run_command", {"command": "echo batch ok"}),
    ]
    events = []
    for index, (tid, name, args) in enumerate(calls):
        events.append(
            json.dumps(
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}},
                }
            )
        )
        events.append(
            json.dumps(
                {
                    "type": "content_block_delta",
                    "index": index,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(args, ensure_ascii=False)},
                },
                ensure_ascii=False,
            )
        )
        events.append(json.dumps({"type": "content_block_stop", "index": index}))
    events.append(
        json.dumps(
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use"},
                "usage": {"input_tokens": 100, "output_tokens": 42},
            }
        )
    )
    events.append(json.dumps({"type": "message_stop"}))
    return events


def _openai_tool_round_chunks(
    tool_call_id: str, name: str, args: dict, text: str | None = None
) -> list[str]:
    """一轮 OpenAI 工具调用 chunk（参数一片到位，finish chunk 附 usage）。"""
    chunks = [
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}],
            }
        )
    ]
    if text:
        chunks.append(
            json.dumps(
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                },
                ensure_ascii=False,
            )
        )
    chunks.append(
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": tool_call_id,
                                    "type": "function",
                                    "function": {"name": name, "arguments": ""},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            }
        )
    )
    chunks.append(
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": json.dumps(args, ensure_ascii=False)}}
                            ]
                        },
                        "finish_reason": None,
                    }
                ],
            },
            ensure_ascii=False,
        )
    )
    chunks.append(
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 42},
            }
        )
    )
    chunks.append("data: [DONE]")
    return chunks


def _openai_text_round_chunks(text: str) -> list[str]:
    """一轮 OpenAI 纯文本 chunk（最终回复轮，finish chunk 附 usage）。"""
    return [
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}],
            }
        ),
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 42},
            }
        ),
        "data: [DONE]",
    ]


def _openai_multi_tool_chunks() -> list[str]:
    """单轮三个 tool_calls（read_file + grep_code + run_command，带 index）。"""
    calls = [
        ("call_m1", "read_file", {"file_path": "pyproject.toml"}),
        ("call_m2", "grep_code", {"pattern": "version"}),
        ("call_m3", "run_command", {"command": "echo batch ok"}),
    ]
    chunks = [
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}],
            }
        )
    ]
    for index, (cid, name, args) in enumerate(calls):
        chunks.append(
            json.dumps(
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": index,
                                        "id": cid,
                                        "type": "function",
                                        "function": {"name": name, "arguments": ""},
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                }
            )
        )
        chunks.append(
            json.dumps(
                {
                    "id": "c1",
                    "object": "chat.completion.chunk",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "tool_calls": [
                                    {"index": index, "function": {"arguments": json.dumps(args, ensure_ascii=False)}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )
    chunks.append(
        json.dumps(
            {
                "id": "c1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 42},
            }
        )
    )
    chunks.append("data: [DONE]")
    return chunks


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
            elif has_tools and model == "loop-model":
                n = _anthropic_tool_result_count(body)
                if n == 0:
                    self._send_sse(
                        _anthropic_tool_round(
                            "toolu_loop_1", "read_file", {"file_path": "pyproject.toml"},
                            text="我先看看项目结构。",
                        )
                    )
                elif n == 1:
                    self._send_sse(
                        _anthropic_tool_round(
                            "toolu_loop_2", "run_command", {"command": "echo hello from loop"}
                        )
                    )
                else:
                    self._send_sse(_anthropic_text_round("**完成！** 项目没问题。"))
            elif has_tools and model == "multi-tool-model":
                self._send_sse(_anthropic_multi_tool_events())
            elif has_tools and model == "unknown-tool-model":
                self._send_sse(_anthropic_tool_round("toolu_unk", "no_such_tool", {}))
            elif has_tools and model == "never-stop-model":
                self._send_sse(
                    _anthropic_tool_round("toolu_never", "read_file", {"file_path": "pyproject.toml"})
                )
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
            elif has_tools and model == "loop-model":
                n = _openai_tool_result_count(body)
                if n == 0:
                    self._send_sse(
                        _openai_tool_round_chunks(
                            "call_loop_1", "read_file", {"file_path": "pyproject.toml"},
                            text="我先看看项目结构。",
                        )
                    )
                elif n == 1:
                    self._send_sse(
                        _openai_tool_round_chunks(
                            "call_loop_2", "run_command", {"command": "echo hello from loop"}
                        )
                    )
                else:
                    self._send_sse(_openai_text_round_chunks("**完成！** 项目没问题。"))
            elif has_tools and model == "multi-tool-model":
                self._send_sse(_openai_multi_tool_chunks())
            elif has_tools and model == "unknown-tool-model":
                self._send_sse(_openai_tool_round_chunks("call_unk", "no_such_tool", {}))
            elif has_tools and model == "never-stop-model":
                self._send_sse(
                    _openai_tool_round_chunks("call_never", "read_file", {"file_path": "pyproject.toml"})
                )
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
