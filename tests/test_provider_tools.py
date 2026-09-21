"""Provider 工具调用支持测试：流解析（碎片拼接）、请求体 tools 字段、回放重建。"""

import json

import pytest

from mewcode.config import ProviderConfig
from mewcode.provider.anthropic import AnthropicProvider, _build_payload as anthropic_payload
from mewcode.provider.base import Done, SystemPrompt, ToolCall
from mewcode.provider.openai import OpenAIProvider, _build_payload as openai_payload
from mewcode.session import ChatSession, Message, ToolResultRecord, ToolUse
from mewcode.tools import create_registry

# 不关心系统提示的调用点统一传入空 SystemPrompt（请求体省略 system 字段）
_NO_SYSTEM = SystemPrompt("", "")


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
    payload = anthropic_payload(_config(), _tool_session().messages, [], _NO_SYSTEM)
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
    payload = anthropic_payload(_config(), s.messages, [], _NO_SYSTEM)
    result_msg = payload["messages"][2]
    assert result_msg["content"][0]["is_error"] is True


def test_openai_payload_replay_blocks():
    payload = openai_payload(_config("openai"), _tool_session().messages, [], _NO_SYSTEM)
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
    ap = anthropic_payload(_config(), [Message(role="user", text="问")], tools, _NO_SYSTEM)
    assert len(ap["tools"]) == 6
    t = ap["tools"][0]
    assert set(t) == {"name", "description", "input_schema"}
    assert t["input_schema"]["type"] == "object"

    op = openai_payload(_config("openai"), [Message(role="user", text="问")], tools, _NO_SYSTEM)
    assert len(op["tools"]) == 6
    f = op["tools"][0]
    assert f["type"] == "function"
    assert set(f["function"]) == {"name", "description", "parameters"}


def test_payloads_empty_tools_omitted():
    """tools 为空时不携带 tools 字段（保持既有纯文本请求兼容）。"""
    ap = anthropic_payload(_config(), [Message(role="user", text="问")], [], _NO_SYSTEM)
    assert "tools" not in ap
    op = openai_payload(_config("openai"), [Message(role="user", text="问")], [], _NO_SYSTEM)
    assert "tools" not in op


# ---------------------------------------------------------------------------
# T2: Anthropic message_delta usage 解析
# ---------------------------------------------------------------------------

def test_anthropic_message_delta_usage_in_done():
    """输入与缓存来自 message_start、输出来自 message_delta（真实协议形态）。"""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 123, "cache_creation_input_tokens": 90, "cache_read_input_tokens": 33}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "你好"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 45}},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    events = list(provider._iter_events(_events(events)))
    done = events[-1]
    assert isinstance(done, Done)
    assert done.usage.input_tokens == 123
    assert done.usage.output_tokens == 45
    assert done.usage.cache_hit_tokens == 33
    assert done.usage.cache_write_tokens == 90


def test_anthropic_message_start_cache_fields_optional():
    """message_start 不携带缓存字段时，Done.usage 的缓存字段为 None（优雅降级）。"""
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 10}}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    provider = AnthropicProvider(_config())
    done = list(provider._iter_events(_events(events)))[-1]
    assert isinstance(done, Done)
    assert done.usage.cache_hit_tokens is None
    assert done.usage.cache_write_tokens is None


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


# ---------------------------------------------------------------------------
# ch06: system 块、缓存断点与动态补充
# ---------------------------------------------------------------------------

def test_anthropic_system_blocks_with_breakpoint():
    """stable 块带缓存断点、dynamic 块紧随其后不带断点。"""
    system = SystemPrompt(stable="稳定指令", dynamic="<supplement>动态补充</supplement>")
    payload = anthropic_payload(_config(), [Message(role="user", text="问")], [], system)
    assert payload["system"] == [
        {"type": "text", "text": "稳定指令", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "<supplement>动态补充</supplement>"},
    ]


def test_anthropic_system_single_part():
    """只有 stable 时单块（带断点）；只有 dynamic 时单块（不带断点）。"""
    payload = anthropic_payload(
        _config(), [Message(role="user", text="问")], [], SystemPrompt("稳定", "")
    )
    assert payload["system"] == [
        {"type": "text", "text": "稳定", "cache_control": {"type": "ephemeral"}}
    ]
    payload = anthropic_payload(
        _config(), [Message(role="user", text="问")], [], SystemPrompt("", "动态")
    )
    assert payload["system"] == [{"type": "text", "text": "动态"}]


def test_anthropic_empty_system_omitted():
    """stable/dynamic 均空时请求体不含 system 字段（纯文本请求兼容）。"""
    payload = anthropic_payload(_config(), [Message(role="user", text="问")], [], _NO_SYSTEM)
    assert "system" not in payload


def test_anthropic_last_tool_carries_breakpoint():
    """最后一个工具带缓存断点，其余工具不带——系统提示+工具整体成缓存前缀。"""
    tools = create_registry().list()
    payload = anthropic_payload(_config(), [Message(role="user", text="问")], tools, _NO_SYSTEM)
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    for t in payload["tools"][:-1]:
        assert "cache_control" not in t


# ---------------------------------------------------------------------------
# ch06: OpenAI system 消息与缓存命中解析
# ---------------------------------------------------------------------------

def test_openai_system_message_stable_first():
    """system 消息在消息列表第一条：stable 在最前、dynamic 拼接其后。"""
    system = SystemPrompt(stable="稳定指令", dynamic="<supplement>动态补充</supplement>")
    payload = openai_payload(_config("openai"), [Message(role="user", text="问")], [], system)
    assert payload["messages"][0] == {
        "role": "system",
        "content": "稳定指令\n\n<supplement>动态补充</supplement>",
    }
    assert payload["messages"][1] == {"role": "user", "content": "问"}


def test_openai_system_dynamic_only():
    """只有 dynamic 时 system 消息仅含 dynamic；均空时省略 system 消息。"""
    payload = openai_payload(
        _config("openai"), [Message(role="user", text="问")], [], SystemPrompt("", "动态")
    )
    assert payload["messages"][0] == {"role": "system", "content": "动态"}
    payload = openai_payload(_config("openai"), [Message(role="user", text="问")], [], _NO_SYSTEM)
    assert payload["messages"][0] == {"role": "user", "content": "问"}


def test_openai_prompt_cache_hit_parsed():
    """usage 的 prompt_cache_hit_tokens 归一化为 cache_hit_tokens；miss 丢弃。"""
    chunks = [
        {"id": "c1", "choices": [{"index": 0, "delta": {"content": "你好"}, "finish_reason": None}]},
        {
            "id": "c1",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 8,
                "prompt_cache_hit_tokens": 400,
                "prompt_cache_miss_tokens": 100,
            },
        },
    ]
    provider = OpenAIProvider(_config("openai"))
    done = list(provider._iter_events(_events(chunks)))[-1]
    assert isinstance(done, Done)
    assert done.usage.input_tokens == 500
    assert done.usage.output_tokens == 8
    assert done.usage.cache_hit_tokens == 400
    assert done.usage.cache_write_tokens is None
