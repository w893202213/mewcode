"""摘要 Prompt 构建与解析测试（ch09 F4）。"""

import pytest

from mewcode.context.prompts import (
    BOUNDARY_NOTICE,
    SUMMARY_SYSTEM,
    SummarizeError,
    build_summarize_request,
    parse_summary,
)
from mewcode.session import Message, ToolResultRecord, ToolUse


def test_system_instruction_contains_constraints():
    assert "禁止调用任何工具" in SUMMARY_SYSTEM
    for part in (
        "总体目标与状态",
        "已完成工作",
        "关键文件与代码",
        "重要事实与决策",
        "未完成事项",
        "注意事项",
    ):
        assert part in SUMMARY_SYSTEM
    assert "<分析草稿>" in SUMMARY_SYSTEM and "<正式摘要>" in SUMMARY_SYSTEM


def test_request_single_user_message_with_chunks():
    msgs = [
        Message(role="user", text="帮我修 bug"),
        Message(
            role="assistant",
            text="我来看看",
            tool_uses=[ToolUse(id="1", name="read", args={"path": "a.py"})],
        ),
        Message(
            role="user",
            text="",
            tool_results=[ToolResultRecord(tool_use_id="1", content="文件内容", is_error=False)],
        ),
    ]
    system, request = build_summarize_request(msgs)
    assert system.stable == SUMMARY_SYSTEM and system.dynamic == ""
    assert len(request) == 1 and request[0].role == "user"
    text = request[0].text
    assert "[用户消息]" in text and "帮我修 bug" in text
    assert "[助手回复]" in text and "[工具调用] read" in text
    assert "[工具结果]" in text and "文件内容" in text


def test_request_chunk_truncated():
    msgs = [Message(role="user", text="x" * 5000)]
    _, request = build_summarize_request(msgs)
    assert "已截断" in request[0].text
    assert "x" * 5000 not in request[0].text


def test_parse_summary_keeps_formal_discards_draft():
    text = "<分析草稿>\n这是草稿，应该被丢弃\n</分析草稿>\n<正式摘要>\n## 总体目标与状态\n做完了\n</正式摘要>"
    summary = parse_summary(text)
    assert "草稿" not in summary
    assert "总体目标与状态" in summary and "做完了" in summary


@pytest.mark.parametrize("text", ["没有标记的文本", "<分析草稿>x</分析草稿>", "<正式摘要>   </正式摘要>"])
def test_parse_summary_missing_or_empty_raises(text):
    with pytest.raises(SummarizeError):
        parse_summary(text)


def test_boundary_notice_mentions_reread():
    assert "重新读取文件" in BOUNDARY_NOTICE
    assert "摘要" in BOUNDARY_NOTICE
