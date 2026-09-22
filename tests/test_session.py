"""会话层测试。"""

from mewcode.session import ChatSession


def test_add_user_and_assistant_order():
    s = ChatSession()
    s.add_user("你好")
    s.add_assistant("你好！")
    s.add_user("再见")
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "你好"),
        ("assistant", "你好！"),
        ("user", "再见"),
    ]


def test_assistant_thinking_fields():
    s = ChatSession()
    raw = [{"type": "thinking", "thinking": "让我想想", "signature": "sig"}]
    s.add_assistant("答案", thinking="让我想想", thinking_raw=raw)
    m = s.messages[0]
    assert m.thinking == "让我想想"
    assert m.thinking_raw == raw


def test_thinking_defaults_empty():
    s = ChatSession()
    s.add_assistant("无思考")
    m = s.messages[0]
    assert m.thinking == "" and m.thinking_raw == []


def test_clear():
    s = ChatSession()
    s.add_user("a")
    s.add_assistant("b")
    s.clear()
    assert s.messages == []


def test_replace_head_replaces_prefix():
    s = ChatSession()
    s.add_user("u1")
    s.add_assistant("a1")
    s.add_user("u2")
    s.add_assistant("a2")
    s.replace_head(2, [])
    assert [(m.role, m.text) for m in s.messages] == [("user", "u2"), ("assistant", "a2")]


def test_replace_head_inserts_new_messages():
    from mewcode.session import Message

    s = ChatSession()
    s.add_user("u1")
    s.add_assistant("a1")
    s.add_user("u2")
    s.replace_head(2, [Message(role="user", text="摘要"), Message(role="user", text="边界")])
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "摘要"),
        ("user", "边界"),
        ("user", "u2"),
    ]


def test_replace_head_end_zero_pure_insert():
    from mewcode.session import Message

    s = ChatSession()
    s.add_user("u1")
    s.replace_head(0, [Message(role="user", text="头部")])
    assert [(m.role, m.text) for m in s.messages] == [("user", "头部"), ("user", "u1")]


def test_replace_head_out_of_range_raises():
    import pytest

    s = ChatSession()
    s.add_user("u1")
    with pytest.raises(ValueError):
        s.replace_head(2, [])


def test_restore_replaces_messages():
    from mewcode.session import Message

    s = ChatSession()
    s.add_user("旧消息")
    s.restore([Message(role="user", text="u1"), Message(role="assistant", text="a1")])
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "u1"),
        ("assistant", "a1"),
    ]


def test_restore_copies_input_list():
    from mewcode.session import Message

    s = ChatSession()
    incoming = [Message(role="user", text="u1")]
    s.restore(incoming)
    incoming.append(Message(role="assistant", text="a1"))
    assert len(s.messages) == 1  # 入参后续修改不影响会话


def test_restore_then_add_appends():
    from mewcode.session import Message

    s = ChatSession()
    s.restore([Message(role="user", text="u1")])
    s.add_assistant("a1")
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "u1"),
        ("assistant", "a1"),
    ]
