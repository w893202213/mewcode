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
