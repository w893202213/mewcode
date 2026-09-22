"""自动笔记测试（ch11 F5）：NoteManager 读写、索引截断、记忆块拼接。"""

import time

from mewcode.memory.notes import (
    INDEX_MAX_BYTES,
    INDEX_MAX_LINES,
    NoteChange,
    NoteManager,
    build_memory_block,
)


def _change(name="lang-preference", **kw) -> NoteChange:
    fields = dict(
        action="add",
        note_type="user_preference",
        scope="user",
        name=name,
        description="用户偏好中文回答",
        content="用户希望 MewCode 始终用中文回答。",
    )
    fields.update(kw)
    return NoteChange(**fields)


def test_write_and_read_note_roundtrip(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    slug = manager.write_note(_change())
    assert slug == "lang-preference"
    content = (tmp_path / "memory" / "lang-preference.md").read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "name: lang-preference" in content
    assert "description: 用户偏好中文回答" in content
    assert "type: user_preference" in content
    assert "updated: " in content
    assert "用户希望 MewCode 始终用中文回答。" in content


def test_write_note_invalid_name_gets_timestamp_slug(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    slug = manager.write_note(_change(name="中文/非法 name!"))
    assert slug.startswith("note-")
    assert (tmp_path / "memory" / f"{slug}.md").exists()


def test_index_missing_returns_empty(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    assert manager.index_text() == ""
    assert manager.load_notes() == ""


def test_load_notes_excludes_index_and_truncates(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    manager.write_note(_change(name="a"))
    (tmp_path / "memory" / "index.md").write_text("索引内容", encoding="utf-8")
    notes = manager.load_notes()
    assert "索引内容" not in notes
    assert "--- 笔记: a ---" in notes


def test_write_index_truncates_lines(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    big = "\n".join(f"- [n{i}](n{i}.md) — 说明" for i in range(INDEX_MAX_LINES + 50))
    manager.write_index(big)
    lines = (tmp_path / "memory" / "index.md").read_text().splitlines()
    assert len(lines) == INDEX_MAX_LINES


def test_write_index_truncates_bytes(tmp_path):
    manager = NoteManager(tmp_path / "memory")
    big = "\n".join(f"- [n{i}](n{i}.md) — {'很' * 300}" for i in range(500))
    manager.write_index(big)
    data = (tmp_path / "memory" / "index.md").read_bytes()
    assert len(data) <= INDEX_MAX_BYTES


def test_build_memory_block_both_sides(tmp_path):
    text = build_memory_block("用户索引", "项目索引")
    assert text == "## 用户级记忆\n用户索引\n\n## 项目级记忆\n项目索引"


def test_build_memory_block_single_and_empty(tmp_path):
    assert build_memory_block("用户索引", "") == "## 用户级记忆\n用户索引"
    assert build_memory_block("", "项目索引") == "## 项目级记忆\n项目索引"
    assert build_memory_block("", "") == ""
    assert build_memory_block("  ", "") == ""


# ---- T7: 提示模板与解析 ----


def _good_response() -> str:
    return (
        '{"changes": ['
        '{"action": "add", "type": "user_preference", "scope": "user", '
        '"name": "lang-pref", "description": "偏好中文", "content": "用户偏好中文回答。"}'
        "], "
        '"user_index": "- [lang-pref](lang-pref.md) — 偏好中文\\n", '
        '"project_index": ""}'
    )


def test_parse_normal_response():
    from mewcode.memory.prompts import parse_notes_response

    changes, user_index, project_index = parse_notes_response(_good_response())
    assert len(changes) == 1
    c = changes[0]
    assert (c.action, c.note_type, c.scope, c.name) == (
        "add",
        "user_preference",
        "user",
        "lang-pref",
    )
    assert "lang-pref" in user_index
    assert project_index == ""


def test_parse_fenced_response():
    from mewcode.memory.prompts import parse_notes_response

    changes, _, _ = parse_notes_response("```json\n" + _good_response() + "\n```")
    assert len(changes) == 1


def test_parse_drops_invalid_items():
    from mewcode.memory.prompts import parse_notes_response

    text = (
        '{"changes": ['
        '{"action": "add", "type": "user_preference", "scope": "user", '
        '"name": "ok1", "description": "d", "content": "c"},'
        '{"action": "remove", "type": "user_preference", "scope": "user", "name": "x", "description": "", "content": ""},'
        '{"action": "add", "type": "hobby", "scope": "user", "name": "x", "description": "", "content": ""},'
        '{"action": "add", "type": "correction", "scope": "user"},'
        '"not-a-dict"'
        "], "
        '"user_index": "", "project_index": ""}'
    )
    changes, _, _ = parse_notes_response(text)
    assert [c.name for c in changes] == ["ok1"]


def test_parse_invalid_json_raises():
    import pytest

    from mewcode.memory.prompts import NotesParseError, parse_notes_response

    with pytest.raises(NotesParseError):
        parse_notes_response("随便说点什么")
    with pytest.raises(NotesParseError):
        parse_notes_response('["数组不算"]')


def test_build_request_contains_all_sections():
    from mewcode.memory.prompts import build_notes_request
    from mewcode.session import Message

    text = build_notes_request(
        [Message(role="user", text="你好"), Message(role="assistant", text="你好！")],
        "用户索引",
        "项目索引",
        "用户笔记",
        "项目笔记",
    )
    assert "user: 你好" in text
    assert "assistant: 你好！" in text
    assert "用户级现有笔记" in text and "用户笔记" in text
    assert "项目级现有笔记" in text and "项目笔记" in text
    assert "用户级索引" in text and "项目级索引" in text


# ---- T8: NoteUpdater 异步更新 ----


class FakeProvider:
    """脚本化假 Provider：每次 stream 返回预置事件序列，记录调用内容。"""

    def __init__(self, handler=None):
        self._handler = handler
        self.calls = []

    def stream(self, messages, tools, system):
        self.calls.append((messages, tools, system))
        if self._handler is None:
            return iter([])
        return iter(self._handler(len(self.calls), messages, tools, system))


def _text_provider(text):
    from mewcode.provider.base import TextDelta

    return FakeProvider(lambda *a: [TextDelta(text)])


def _wait_until(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _updater(tmp_path, provider, notice):
    from mewcode.memory.notes import NoteUpdater

    return NoteUpdater(
        provider,
        project=NoteManager(tmp_path / "proj" / ".mewcode" / "memory"),
        user=NoteManager(tmp_path / "home" / ".mewcode" / "memory"),
        notice=notice,
    )


def test_updater_writes_notes_and_indexes(tmp_path):
    from mewcode.session import Message

    response = (
        '{"changes": ['
        '{"action": "add", "type": "user_preference", "scope": "user", '
        '"name": "lang-pref", "description": "偏好中文", "content": "用户偏好中文回答。"},'
        '{"action": "add", "type": "project_knowledge", "scope": "project", '
        '"name": "python-project", "description": "Python 项目", "content": "本项目使用 Python。"}'
        "], "
        '"user_index": "- [lang-pref](lang-pref.md) — 偏好中文\\n", '
        '"project_index": "- [python-project](python-project.md) — Python 项目\\n"}'
    )
    notices: list[str] = []
    updater = _updater(tmp_path, _text_provider(response), notices.append)
    assert updater.submit([Message(role="user", text="对话")]) is True
    assert _wait_until(lambda: not updater._busy)
    # 用户级与项目级分开落盘
    assert (tmp_path / "home" / ".mewcode" / "memory" / "lang-pref.md").exists()
    assert (tmp_path / "proj" / ".mewcode" / "memory" / "python-project.md").exists()
    user_index = (tmp_path / "home" / ".mewcode" / "memory" / "index.md").read_text()
    project_index = (tmp_path / "proj" / ".mewcode" / "memory" / "index.md").read_text()
    assert "lang-pref" in user_index and "lang-pref" not in project_index
    assert "python-project" in project_index
    assert notices == ["📝 记忆已更新：2 条新增 / 0 条更新"]


def test_updater_parse_failure_silent(tmp_path):
    from mewcode.session import Message

    notices: list[str] = []
    updater = _updater(tmp_path, _text_provider("不是 JSON"), notices.append)
    updater.submit([Message(role="user", text="对话")])
    assert _wait_until(lambda: not updater._busy)
    assert notices == ["记忆更新失败，本轮跳过"]
    assert not list((tmp_path / "proj" / ".mewcode" / "memory").glob("*.md"))
    assert not list((tmp_path / "home" / ".mewcode" / "memory").glob("*.md"))


def test_updater_provider_error_silent(tmp_path):
    from mewcode.provider.base import ProviderError
    from mewcode.session import Message

    def failing(*a):
        raise ProviderError("网络错误")

    notices: list[str] = []
    updater = _updater(tmp_path, FakeProvider(failing), notices.append)
    updater.submit([Message(role="user", text="对话")])
    assert _wait_until(lambda: not updater._busy)
    assert notices == ["记忆更新失败，本轮跳过"]


def test_updater_busy_skips_resubmit(tmp_path):
    from mewcode.session import Message

    started = []

    def slow(*a):
        started.append(True)
        time.sleep(0.2)
        return []

    updater = _updater(tmp_path, FakeProvider(slow), None)
    assert updater.submit([Message(role="user", text="a")]) is True
    assert _wait_until(lambda: started)
    # 上一轮还在跑：本轮跳过
    assert updater.submit([Message(role="user", text="b")]) is False
    assert _wait_until(lambda: not updater._busy)
    # 跑完后又可提交
    assert updater.submit([Message(role="user", text="c")]) is True
    assert _wait_until(lambda: not updater._busy)


def test_updater_update_missing_target_treated_as_add(tmp_path):
    from mewcode.session import Message

    response = (
        '{"changes": ['
        '{"action": "update", "type": "correction", "scope": "user", '
        '"name": "style-note", "description": "风格纠正", "content": "回答要简洁。"}'
        "], "
        '"user_index": "- [style-note](style-note.md) — 风格纠正\\n", '
        '"project_index": ""}'
    )
    updater = _updater(tmp_path, _text_provider(response), None)
    updater.submit([Message(role="user", text="对话")])
    assert _wait_until(lambda: not updater._busy)
    assert (tmp_path / "home" / ".mewcode" / "memory" / "style-note.md").exists()


def test_updater_none_action_skips_write(tmp_path):
    from mewcode.session import Message

    response = (
        '{"changes": [{"action": "none", "type": "reference", "scope": "user", '
        '"name": "x", "description": "", "content": ""}], '
        '"user_index": "", "project_index": ""}'
    )
    updater = _updater(tmp_path, _text_provider(response), None)
    updater.submit([Message(role="user", text="对话")])
    assert _wait_until(lambda: not updater._busy)
    # 无笔记文件（只有每次更新都会写的 index.md）
    files = [f.name for f in (tmp_path / "home" / ".mewcode" / "memory").glob("*.md")]
    assert files == ["index.md"]
