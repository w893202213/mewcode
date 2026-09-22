"""Token 估算器测试（ch09 F2）。"""

from mewcode.context.estimator import TokenEstimator, message_chars
from mewcode.provider.base import SystemPrompt
from mewcode.session import ChatSession, Message, ToolResultRecord, ToolUse

SYSTEM = SystemPrompt(stable="系统提示", dynamic="补充")


def _est(messages: list[Message]) -> int:
    return TokenEstimator().estimate(messages, SYSTEM)


def test_no_anchor_full_chars_estimate():
    """无锚点：全量（消息 + stable + dynamic）字符 ÷4。"""
    s = ChatSession()
    s.add_user("你好世界")  # 4 字符
    # 消息 4 + stable 4 + dynamic 2 = 10 字符 → 2 token
    assert _est(s.messages) == 2


def test_anchor_uses_usage_value():
    """有锚点且无增量：估算等于锚点 input_tokens（系统提示已含在锚点里）。"""
    s = ChatSession()
    s.add_user("你好世界")
    e = TokenEstimator()
    e.anchor(1000, s.messages)
    assert e.estimate(s.messages, SYSTEM) == 1000


def test_anchor_plus_increment():
    """有锚点追加消息：估算 = 锚点 + 增量字符 ÷4。"""
    s = ChatSession()
    s.add_user("你好")
    e = TokenEstimator()
    e.anchor(1000, s.messages)
    s.add_assistant("一二三四五六七八")  # 8 字符 → 2 token
    assert e.estimate(s.messages, SYSTEM) == 1002


def test_anchor_shrinks_after_compress():
    """压缩替换头部（字符总量下降）：估算回到锚点值，不虚高。"""
    s = ChatSession()
    s.add_user("用户一")
    s.add_assistant("长回复" * 100)  # 大量字符
    e = TokenEstimator()
    e.anchor(1000, s.messages)  # 锚点覆盖长历史
    s.replace_head(2, [Message(role="user", text="摘要")])  # 压掉长回复
    assert e.estimate(s.messages, SYSTEM) == 1000


def test_message_chars_covers_all_fields():
    """字符口径：text + thinking + 工具结果 + 工具参数 JSON。"""
    m = Message(
        role="assistant",
        text="正文",  # 2
        thinking="思考",  # 2
        tool_uses=[ToolUse(id="1", name="t", args={"path": "ab"})],  # {"path": "ab"} = 14
        tool_results=[ToolResultRecord(tool_use_id="1", content="结果", is_error=False)],  # 2
    )
    assert message_chars(m) == 2 + 2 + 14 + 2
