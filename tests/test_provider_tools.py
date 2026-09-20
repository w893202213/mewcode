"""Provider 工具调用支持测试：流解析（碎片拼接）、请求体 tools 字段、回放重建。"""

import json

import pytest

from mewcode.config import ProviderConfig
from mewcode.provider.anthropic import AnthropicProvider, _build_payload as anthropic_payload
from mewcode.provider.base import Done, ToolCall
from mewcode.provider.openai import OpenAIProvider, _build_payload as openai_payload
from mewcode.session import ChatSession, Message, ToolResultRecord, ToolUse
from mewcode.tools import create_registry


def _config(protocol: str = "anthropic") -> ProviderConfig:
    return ProviderConfig(
        name="test",
        protocol=protocol,
        model="test-model",
        base_url="http://127.0.0.1:1",
        api_key="test-key",
    )


def _events(objects: list[dict]):
    """把 dict 列表转成 provider 解析器期望的对象迭代器（省去 JSON 序列化）。"""
    return iter(objects)


# ---------------------------------------------------------------------------
# T9: Anthropic tool_use 流解析
# ---------------------------------------------------------------------------

ANTHROPIC_TOOL_EVENTS = [
    {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_mock_1", "name": "read_file", "input": {}}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"file_'}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'path": "a.'}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": 'py", "limit":'}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": " 10}"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_stop"},
]


def test_anthropic_tool_call_fragments_join():
    provider = AnthropicProvider(_config())
    events = list(provider._iter_events(_events(ANTHROPIC_TOOL_EVENTS)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    call = calls[0]
    assert call.id == "toolu_mock_1"
    assert call.name == "read_file"
    assert call.args == {"file_path": "a.py", "limit": 10}
    assert call.parse_error is None
    assert isinstance(events[-1], Done)


def test_anthropic_tool_call_bad_json_parse_error():
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_bad", "name": "read_file", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"file_path": '}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "坏掉的"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    events = list(provider._iter_events(_events(events)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].parse_error is not None
    assert "解析失败" in calls[0].parse_error
    assert calls[0].args == {}


def test_anthropic_text_and_tool_call_mixed():
    """正文与工具调用混合的流：文本增量正常产出，工具调用照常解析。"""
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "我来读一下"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_2", "name": "grep_code", "input": {}}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"pattern": "def"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    events = list(provider._iter_events(_events(events)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].name == "grep_code"
    assert calls[0].args == {"pattern": "def"}


# ---------------------------------------------------------------------------
# T10: OpenAI tool_calls 碎片聚合
# ---------------------------------------------------------------------------

OPENAI_TOOL_CHUNKS = [
    {"id": "c1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": None}, "finish_reason": None}]},
    {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "read_file", "arguments": ""}}]}, "finish_reason": None}]},
    {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_'}}]}, "finish_reason": None}]},
    {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'path": "a.'}}]}, "finish_reason": None}]},
    {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'py"}'}}]}, "finish_reason": None}]},
    {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
]


def test_openai_tool_call_fragments_join():
    provider = OpenAIProvider(_config("openai"))
    events = list(provider._iter_events(_events(OPENAI_TOOL_CHUNKS)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    call = calls[0]
    assert call.id == "call_1"
    assert call.name == "read_file"
    assert call.args == {"file_path": "a.py"}
    assert isinstance(events[-1], Done)


def test_openai_tool_call_bad_json_parse_error():
    chunks = [
        {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_bad", "type": "function", "function": {"name": "run_command", "arguments": ""}}]}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{oops"}}]}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    provider = OpenAIProvider(_config("openai"))
    events = list(provider._iter_events(_events(chunks)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert len(calls) == 1
    assert calls[0].parse_error is not None
    assert "解析失败" in calls[0].parse_error


def test_openai_multiple_tool_calls_by_index():
    """多工具调用按 index 分开聚合（F8 场景的数据源）。"""
    chunks = [
        {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call_a", "type": "function", "function": {"name": "read_file", "arguments": ""}}]}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"file_path": "a.py"}'}}, {"index": 1, "id": "call_b", "type": "function", "function": {"name": "grep_code", "arguments": ""}}]}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 1, "function": {"arguments": '{"pattern": "x"}'}}]}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    provider = OpenAIProvider(_config("openai"))
    events = list(provider._iter_events(_events(chunks)))
    calls = [e for e in events if isinstance(e, ToolCall)]
    assert [c.name for c in calls] == ["read_file", "grep_code"]
    assert calls[0].args == {"file_path": "a.py"}
    assert calls[1].args == {"pattern": "x"}


# ---------------------------------------------------------------------------
# T11: 回放重建
# ---------------------------------------------------------------------------


def _tool_session() -> ChatSession:
    s = ChatSession()
    s.add_user("看看 a.py")
    s.add_assistant("", tool_uses=[ToolUse(id="toolu_1", name="read_file", args={"file_path": "a.py"})])
    s.add_tool_result(ToolResultRecord("toolu_1", "x = 1", is_error=False))
    s.add_user("再改一下")
    return s


def test_anthropic_payload_replay_blocks():
    payload = anthropic_payload(_config(), _tool_session().messages, [])
    msgs = payload["messages"]
    # assistant 消息：tool_use 块 + text 块
    assistant = msgs[1]
    assert assistant["role"] == "assistant"
    blocks = assistant["content"]
    tool_use = next(b for b in blocks if b["type"] == "tool_use")
    assert tool_use == {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"file_path": "a.py"}}
    assert blocks[-1]["type"] == "text"
    # user 消息：tool_result 块
    result_msg = msgs[2]
    assert result_msg["role"] == "user"
    assert result_msg["content"] == [
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "x = 1", "is_error": False}
    ]
    # 下一条普通 user 消息不受影响
    assert msgs[3] == {"role": "user", "content": "再改一下"}


def test_anthropic_payload_error_result_is_error():
    s = ChatSession()
    s.add_user("跑命令")
    s.add_assistant("", tool_uses=[ToolUse(id="toolu_2", name="run_command", args={"command": "rm x"})])
    s.add_tool_result(ToolResultRecord("toolu_2", "用户拒绝", is_error=True))
    payload = anthropic_payload(_config(), s.messages, [])
    result_msg = payload["messages"][2]
    assert result_msg["content"][0]["is_error"] is True


def test_openai_payload_replay_blocks():
    payload = openai_payload(_config("openai"), _tool_session().messages, [])
    msgs = payload["messages"]
    # assistant 消息：content 置空 + tool_calls
    assistant = msgs[1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] is None
    assert assistant["tool_calls"] == [
        {
            "id": "toolu_1",
            "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"file_path": "a.py"}, ensure_ascii=False)},
        }
    ]
    # 工具结果：tool 角色消息
    tool_msg = msgs[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == "toolu_1"
    assert tool_msg["content"] == "x = 1"
    assert msgs[3] == {"role": "user", "content": "再改一下"}


def test_payloads_include_tools_field():
    registry = create_registry()
    tools = registry.list()
    ap = anthropic_payload(_config(), [Message(role="user", text="问")], tools)
    assert len(ap["tools"]) == 6
    t = ap["tools"][0]
    assert set(t) == {"name", "description", "input_schema"}
    assert t["input_schema"]["type"] == "object"

    op = openai_payload(_config("openai"), [Message(role="user", text="问")], tools)
    assert len(op["tools"]) == 6
    f = op["tools"][0]
    assert f["type"] == "function"
    assert set(f["function"]) == {"name", "description", "parameters"}


def test_payloads_empty_tools_omitted():
    """tools 为空时不携带 tools 字段（保持既有纯文本请求兼容）。"""
    ap = anthropic_payload(_config(), [Message(role="user", text="问")], [])
    assert "tools" not in ap
    op = openai_payload(_config("openai"), [Message(role="user", text="问")], [])
    assert "tools" not in op


# ---------------------------------------------------------------------------
# T2: Anthropic message_delta usage 解析
# ---------------------------------------------------------------------------

def test_anthropic_message_delta_usage_in_done():
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"input_tokens": 123, "output_tokens": 45}},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    events = list(provider._iter_events(_events(events)))
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage.input_tokens == 123
    assert done.usage.output_tokens == 45


def test_anthropic_usage_none_without_message_delta():
    events = [
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    done = list(provider._iter_events(_events(events)))[-1]
    assert isinstance(done, Done)
    assert done.usage is None


# ---------------------------------------------------------------------------
# T3: OpenAI usage 解析
# ---------------------------------------------------------------------------

def test_openai_usage_on_finish_chunk_in_done():
    chunks = [
        {"id": "c1", "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
        {
            "id": "c1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 77, "completion_tokens": 8},
        },
    ]
    provider = OpenAIProvider(_config("openai"))
    done = list(provider._iter_events(_events(chunks)))[-1]
    assert isinstance(done, Done)
    assert done.usage.input_tokens == 77
    assert done.usage.output_tokens == 8


def test_openai_usage_none_without_usage_chunk():
    chunks = [
        {"id": "c1", "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
        {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    provider = OpenAIProvider(_config("openai"))
    done = list(provider._iter_events(_events(chunks)))[-1]
    assert isinstance(done, Done)
    assert done.usage is None
