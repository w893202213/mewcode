"""会话层工具记录测试。"""

from mewcode.session import ChatSession, ToolResultRecord, ToolUse


def test_add_assistant_with_tool_uses():
    s = ChatSession()
    use = ToolUse(id="toolu_1", name="read_file", args={"file_path": "a.py"})
    s.add_assistant("", tool_uses=[use])
    m = s.messages[0]
    assert m.role == "assistant"
    assert m.tool_uses == [use]
    assert m.tool_results == []


def test_add_assistant_default_tool_fields_empty():
    s = ChatSession()
    s.add_assistant("普通回复")
    m = s.messages[0]
    assert m.tool_uses == []
    assert m.tool_results == []


def test_add_tool_result_appends_user_message():
    s = ChatSession()
    s.add_user("帮我读文件")
    record = ToolResultRecord(tool_use_id="toolu_1", content="文件内容", is_error=False)
    s.add_tool_result(record)
    assert [(m.role, m.text) for m in s.messages] == [
        ("user", "帮我读文件"),
        ("user", ""),
    ]
    m = s.messages[-1]
    assert m.tool_results == [record]
    assert m.tool_uses == []


def test_tool_round_full_sequence():
    """完整工具轮次：user 提问 → assistant 工具调用 → user 工具结果。"""
    s = ChatSession()
    s.add_user("看看 README 第一行")
    use = ToolUse(id="toolu_1", name="read_file", args={"file_path": "README.md"})
    s.add_assistant("", tool_uses=[use])
    s.add_tool_result(ToolResultRecord("toolu_1", "# MewCode", is_error=False))
    assert [m.role for m in s.messages] == ["user", "assistant", "user"]
    assert s.messages[1].tool_uses[0].name == "read_file"
    assert s.messages[2].tool_results[0].content == "# MewCode"
