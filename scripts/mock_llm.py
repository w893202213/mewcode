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
- ch08 MCP e2e：
  - "mcp-model"         → 第一轮调用 MCP 工具 add（1+2），回灌后出最终文本

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


def _anthropic_message_start(body: dict) -> str:
    """message_start 事件：首轮写缓存、后续轮读缓存（模拟真实缓存行为，供 e2e 观察）。

    真实协议里输入与缓存字段在 message_start 携带；首轮请求无回灌结果
    （首次建立缓存前缀），后续轮有工具结果回灌（前缀命中）。
    """
    n = _anthropic_tool_result_count(body)
    if n == 0:
        usage = {
            "input_tokens": 800, "output_tokens": 1,
            "cache_creation_input_tokens": 600, "cache_read_input_tokens": 0,
        }
    else:
        usage = {
            "input_tokens": 800, "output_tokens": 1,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 600,
        }
    return json.dumps(
        {
            "type": "message_start",
            "message": {
                "id": "msg_mock", "type": "message", "role": "assistant",
                "model": body.get("model", ""), "content": [], "usage": usage,
            },
        },
        ensure_ascii=False,
    )


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
                "usage": {"output_tokens": 42},
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
                "usage": {"output_tokens": 42},
            }
        ),
        json.dumps({"type": "message_stop"}),
    ]


# ch09 e2e：摘要响应——分析草稿段（用后即弃）+ 正式摘要段（6 固定部分）
SUMMARY_TEXT = (
    "<分析草稿>\n历史要点：用户正在使用 MewCode 处理项目任务，多轮工具调用已完成。\n</分析草稿>\n"
    "<正式摘要>\n"
    "## 总体目标与状态\n用户正在使用 MewCode 处理项目任务。\n"
    "## 已完成工作\n- 完成多轮工具调用（读取文件、执行命令）\n"
    "## 关键文件与代码\n- pyproject.toml\n"
    "## 重要事实与决策\n- 无\n"
    "## 未完成事项\n- 继续当前任务\n"
    "## 注意事项\n- 以上为历史摘要，非原始对话\n"
    "</正式摘要>"
)


def _is_summary_request(body: dict) -> bool:
    """ch09 e2e：tools 为空且系统指令含「会话历史压缩器」→ 摘要请求。

    Anthropic 的 system 是块列表；OpenAI 兼容服务把系统提示放进
    messages 里 role=system 的消息。
    """
    system_texts = []
    system = body.get("system")
    if isinstance(system, list):
        system_texts.extend(
            b.get("text", "") for b in system if isinstance(b, dict)
        )
    for m in body.get("messages") or []:
        if isinstance(m, dict) and m.get("role") == "system":
            system_texts.append(str(m.get("content", "")))
    return "会话历史压缩器" in "".join(system_texts)


# multi-tool-model 的三个调用（read_file + grep_code + run_command）
MULTI_TOOL_CALLS = [
    ("toolu_m1", "read_file", {"file_path": "pyproject.toml"}),
    ("toolu_m2", "grep_code", {"pattern": "version"}),
    ("toolu_m3", "run_command", {"command": "echo batch ok"}),
]
# danger-model 的三个调用：黑名单（sudo rm -rf /）+ 沙箱越界 + 项目内写入（ch07 e2e）
DANGER_CALLS = [
    ("toolu_d1", "run_command", {"command": "sudo rm -rf /"}),
    ("toolu_d2", "write_file", {"file_path": "../escape.txt", "content": "escape"}),
    ("toolu_d3", "write_file", {"file_path": "note_ok.txt", "content": "ok"}),
]


def _anthropic_multi_tool_events(calls: list = MULTI_TOOL_CALLS) -> list[str]:
    """单轮多个 tool_use 块（参数化调用列表）。"""
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
                "usage": {"output_tokens": 42},
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
                "usage": {"prompt_tokens": 500, "completion_tokens": 42, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 100},
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
                "usage": {"prompt_tokens": 500, "completion_tokens": 42, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 100},
            }
        ),
        "data: [DONE]",
    ]


def _openai_multi_tool_chunks(calls: list = MULTI_TOOL_CALLS) -> list[str]:
    """单轮多个 tool_calls（参数化调用列表，带 index）。"""
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
                "usage": {"prompt_tokens": 500, "completion_tokens": 42, "prompt_cache_hit_tokens": 400, "prompt_cache_miss_tokens": 100},
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

        def send(events: list[str]) -> None:
            """Anthropic 响应统一前置 message_start（携带输入与缓存用量，真实协议形态）。"""
            self._send_sse([_anthropic_message_start(body)] + events)

        if self.path.endswith("/v1/messages"):
            if model == "error-model":
                self._send_sse(['{"type":"error","error":{"type":"overloaded_error","message":"mock 服务过载"}}'])
            elif model == "unauthorized-model":
                self._send_json(401, {"error": {"message": "mock invalid api key"}})
            elif has_tools and model == "tool-model":
                send(ANTHROPIC_TOOL_EVENTS)
            elif has_tools and model == "tool-cmd-model":
                send(ANTHROPIC_CMD_EVENTS)
            elif has_tools and model == "tool-badjson-model":
                send(ANTHROPIC_BADJSON_EVENTS)
            elif has_tools and model == "loop-model":
                n = _anthropic_tool_result_count(body)
                if n == 0:
                    send(
                        _anthropic_tool_round(
                            "toolu_loop_1", "read_file", {"file_path": "pyproject.toml"},
                            text="我先看看项目结构。",
                        )
                    )
                elif n == 1:
                    send(
                        _anthropic_tool_round(
                            "toolu_loop_2", "run_command", {"command": "echo hello from loop"}
                        )
                    )
                else:
                    send(_anthropic_text_round("**完成！** 项目没问题。"))
            elif has_tools and model == "multi-tool-model":
                send(_anthropic_multi_tool_events())
            elif has_tools and model == "mcp-model":
                # ch08 e2e：第一轮调用 MCP 工具 add，回灌后出最终文本
                n = _anthropic_tool_result_count(body)
                if n == 0:
                    send(
                        _anthropic_tool_round(
                            "toolu_mcp_1", "add", {"a": 1, "b": 2},
                            text="我用 MCP 的 add 工具算一下。",
                        )
                    )
                else:
                    send(_anthropic_text_round("**完成！** MCP 工具链可用，结果已回灌。"))
            elif has_tools and model == "danger-model":
                send(_anthropic_multi_tool_events(DANGER_CALLS))
            elif has_tools and model == "unknown-tool-model":
                send(_anthropic_tool_round("toolu_unk", "no_such_tool", {}))
            elif has_tools and model == "never-stop-model":
                send(
                    _anthropic_tool_round("toolu_never", "read_file", {"file_path": "pyproject.toml"})
                )
            elif has_tools and model == "compact-model":
                # ch09 e2e：每轮对话读一次大文件，多轮累积触发自动压缩
                n = _anthropic_tool_result_count(body)
                if n < 6:
                    send(
                        _anthropic_tool_round(
                            f"toolu_c{n}", "read_file", {"file_path": "pyproject.toml"},
                            text="我读一下文件。",
                        )
                    )
                else:
                    send(_anthropic_text_round("**完成！** 上下文压缩 e2e 通过。"))
            elif not has_tools and _is_summary_request(body):
                # ch09 e2e：上下文压缩的摘要调用（tools 为空 + 摘要指令）
                send(_anthropic_text_round(SUMMARY_TEXT))
            elif "thinking" in body:
                send(ANTHROPIC_EVENTS)
            else:
                # 请求未启用 thinking：只返回文本块（真实 API 行为一致）
                send(ANTHROPIC_TEXT_ONLY_EVENTS)
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
            elif has_tools and model == "danger-model":
                self._send_sse(_openai_multi_tool_chunks(DANGER_CALLS))
            elif has_tools and model == "unknown-tool-model":
                self._send_sse(_openai_tool_round_chunks("call_unk", "no_such_tool", {}))
            elif has_tools and model == "never-stop-model":
                self._send_sse(
                    _openai_tool_round_chunks("call_never", "read_file", {"file_path": "pyproject.toml"})
                )
            elif has_tools and model == "compact-model":
                # ch09 e2e：每轮对话读一次大文件，多轮累积触发自动压缩
                n = _openai_tool_result_count(body)
                if n < 6:
                    self._send_sse(
                        _openai_tool_round_chunks(
                            f"call_c{n}", "read_file", {"file_path": "pyproject.toml"},
                            text="我读一下文件。",
                        )
                    )
                else:
                    self._send_sse(_openai_text_round_chunks("**完成！** 上下文压缩 e2e 通过。"))
            elif not has_tools and _is_summary_request(body):
                # ch09 e2e：上下文压缩的摘要调用（tools 为空 + 摘要指令）
                self._send_sse(_openai_text_round_chunks(SUMMARY_TEXT))
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
