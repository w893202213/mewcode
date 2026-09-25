"""记忆召回测试（ch17 渐进式披露·方案 A）：判相关提示组装/解析、
RecallMemoryTool 执行路径、索引瘦身后新上限截断。"""

import pytest

from mewcode.memory.notes import INDEX_MAX_BYTES, INDEX_MAX_LINES, NoteChange, NoteManager
from mewcode.memory.prompts import (
    RecallHit,
    RecallParseError,
    build_recall_request,
    parse_recall_response,
)
from mewcode.memory.recall import RecallMemoryTool
from mewcode.provider.base import ProviderError, TextDelta


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用) → 事件列表（同 test_memory_integration）。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0

    def stream(self, messages, tools, system):
        self.calls += 1
        return iter(self._handler(self.calls))


def _tool(tmp_path, handler):
    """构造召回工具：用户/项目两级目录 + 假 provider。"""
    return RecallMemoryTool(
        FakeProvider(handler), tmp_path / "user-mem", tmp_path / "project-mem"
    )


def _write_note(manager: NoteManager, slug: str, content: str = "正文") -> None:
    manager.write_note(
        NoteChange(
            action="add",
            note_type="project_knowledge",
            scope="user",
            name=slug,
            description="一句话摘要",
            content=content,
        )
    )


# ---- T1: build_recall_request ----


def test_build_recall_request_contains_query_and_indexes():
    text = build_recall_request("查测试状态", "- [a](a.md) — 摘要A", "- [b](b.md) — 摘要B")
    assert "查测试状态" in text
    assert "摘要A" in text
    assert "摘要B" in text
    assert "【查询】" in text and "【用户级索引】" in text and "【项目级索引】" in text


def test_build_recall_request_empty_indexes_show_placeholder():
    text = build_recall_request("q", "", "")
    assert "（无）" in text


# ---- T1: parse_recall_response ----


def test_parse_normal_json():
    hits = parse_recall_response('{"notes": [{"scope": "user", "slug": "a"}]}')
    assert hits == [RecallHit("user", "a")]


def test_parse_fenced_json():
    hits = parse_recall_response('```json\n{"notes": [{"scope": "project", "slug": "b"}]}\n```')
    assert hits == [RecallHit("project", "b")]


def test_parse_invalid_json_raises():
    with pytest.raises(RecallParseError):
        parse_recall_response("这不是 JSON")


def test_parse_drops_invalid_entries():
    hits = parse_recall_response(
        '{"notes": ['
        '{"scope": "global", "slug": "x"},'
        '{"scope": "user", "slug": 42},'
        '{"scope": "user", "slug": "ok"}]}'
    )
    assert hits == [RecallHit("user", "ok")]


def test_parse_truncates_to_three():
    import json

    notes = [{"scope": "user", "slug": f"n{i}"} for i in range(5)]
    hits = parse_recall_response(json.dumps({"notes": notes}))
    assert len(hits) == 3


def test_parse_deduplicates():
    hits = parse_recall_response(
        '{"notes": [{"scope": "user", "slug": "a"}, {"scope": "user", "slug": "a"}]}'
    )
    assert hits == [RecallHit("user", "a")]


def test_parse_strips_md_suffix():
    hits = parse_recall_response('{"notes": [{"scope": "user", "slug": "a.md"}]}')
    assert hits == [RecallHit("user", "a")]


def test_parse_missing_notes_key_returns_empty():
    assert parse_recall_response('{"foo": 1}') == []


# ---- T3: RecallMemoryTool.execute ----


def test_execute_empty_indexes_returns_no_memory_without_llm_call(tmp_path):
    provider = FakeProvider(lambda n: (_ for _ in ()).throw(AssertionError("不应调用")))
    tool = RecallMemoryTool(provider, tmp_path / "u", tmp_path / "p")
    result = tool.execute({"query": "任意查询"})
    assert result.ok
    assert result.content == "没有找到相关记忆"
    assert provider.calls == 0


def test_execute_empty_query_fails_without_llm_call(tmp_path):
    provider = FakeProvider(lambda n: (_ for _ in ()).throw(AssertionError("不应调用")))
    tool = RecallMemoryTool(provider, tmp_path / "u", tmp_path / "p")
    result = tool.execute({"query": "   "})
    assert not result.ok
    assert "query 必须是非空的检索描述" in result.content
    assert provider.calls == 0


def test_execute_recalls_notes_with_source_labels(tmp_path):
    user = NoteManager(tmp_path / "user-mem")
    project = NoteManager(tmp_path / "project-mem")
    _write_note(user, "user-pref", "用户希望中文回答。")
    _write_note(project, "repo-hygiene", "仓库卫生：.mewcode 未 gitignore。")
    user.write_index("- [user-pref](user-pref.md) — 用户偏好中文回答")
    project.write_index("- [repo-hygiene](repo-hygiene.md) — 仓库卫生问题")
    reply = (
        '{"notes": [{"scope": "user", "slug": "user-pref"},'
        ' {"scope": "project", "slug": "repo-hygiene"}]}'
    )
    tool = _tool(tmp_path, lambda n: [TextDelta(reply)])
    result = tool.execute({"query": "有什么偏好和仓库问题"})
    assert result.ok
    assert "用户希望中文回答。" in result.content
    assert "仓库卫生" in result.content
    assert "（用户级记忆）" in result.content
    assert "（项目级记忆）" in result.content
    assert "## 笔记: user-pref" in result.content


def test_execute_llm_error_returns_failure(tmp_path):
    NoteManager(tmp_path / "user-mem").write_index("- [a](a.md) — 摘要A")

    def handler(n):
        raise ProviderError("网络错误")

    tool = _tool(tmp_path, handler)
    result = tool.execute({"query": "查一下"})
    assert not result.ok
    assert "记忆检索失败" in result.content


def test_execute_parse_error_returns_no_memory(tmp_path):
    NoteManager(tmp_path / "user-mem").write_index("- [a](a.md) — 摘要A")
    tool = _tool(tmp_path, lambda n: [TextDelta("垃圾输出")])
    result = tool.execute({"query": "查一下"})
    assert result.ok
    assert result.content == "没有找到相关记忆"


def test_execute_no_relevant_hits_returns_no_memory(tmp_path):
    NoteManager(tmp_path / "user-mem").write_index("- [a](a.md) — 摘要A")
    tool = _tool(tmp_path, lambda n: [TextDelta('{"notes": []}')])
    result = tool.execute({"query": "查一下"})
    assert result.ok
    assert result.content == "没有找到相关记忆"


def test_execute_missing_note_file_skipped(tmp_path):
    NoteManager(tmp_path / "user-mem").write_index("- [ghost](ghost.md) — 不存在的笔记")
    tool = _tool(tmp_path, lambda n: [TextDelta('{"notes": [{"scope": "user", "slug": "ghost"}]}')])
    result = tool.execute({"query": "查一下"})
    assert result.ok
    assert result.content == "没有找到相关记忆"


def test_execute_partial_missing_keeps_existing(tmp_path):
    user = NoteManager(tmp_path / "user-mem")
    _write_note(user, "real", "真实笔记正文")
    user.write_index("- [real](real.md) — 真实\n- [ghost](ghost.md) — 幽灵")
    reply = (
        '{"notes": [{"scope": "user", "slug": "ghost"},'
        ' {"scope": "user", "slug": "real"}]}'
    )
    tool = _tool(tmp_path, lambda n: [TextDelta(reply)])
    result = tool.execute({"query": "查一下"})
    assert result.ok
    assert "真实笔记正文" in result.content
    assert "ghost" not in result.content


def test_execute_rejects_path_traversal_slug(tmp_path):
    NoteManager(tmp_path / "user-mem").write_index("- [a](a.md) — 摘要A")
    reply = '{"notes": [{"scope": "user", "slug": "../../etc/passwd"}]}'
    tool = _tool(tmp_path, lambda n: [TextDelta(reply)])
    result = tool.execute({"query": "查一下"})
    assert result.ok
    assert result.content == "没有找到相关记忆"


# ---- T2: 索引瘦身后新上限 ----


def test_write_index_truncates_to_new_line_limit(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    manager.write_index("\n".join(f"- [n{i}](n{i}.md) — 摘要" for i in range(150)))
    lines = manager.index_text().strip().splitlines()
    assert len(lines) == INDEX_MAX_LINES
    assert INDEX_MAX_LINES == 100


def test_write_index_truncates_to_new_byte_limit(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    manager.write_index("\n".join(f"- [n{i}](n{i}.md) — 摘要" + "长" * 200 for i in range(100)))
    data = manager.index_text().encode("utf-8")
    assert len(data) <= INDEX_MAX_BYTES
    assert INDEX_MAX_BYTES == 12 * 1024
