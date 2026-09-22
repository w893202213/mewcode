"""结果存盘器测试（ch09 F3）。"""

import pytest

from mewcode.context.store import ResultStore, StoreError


def test_save_writes_full_content(tmp_path):
    store = ResultStore(tmp_path / "ctx")
    path = store.save("完整内容" * 100, 3)
    assert path.name == "tool_result_3.txt"
    assert path.read_text(encoding="utf-8") == "完整内容" * 100


def test_save_seq_increment_files(tmp_path):
    store = ResultStore(tmp_path / "ctx")
    p1 = store.save("a", 1)
    p2 = store.save("b", 2)
    assert p1 != p2
    assert {p.name for p in (p1, p2)} == {"tool_result_1.txt", "tool_result_2.txt"}


def test_preview_format(tmp_path):
    store = ResultStore(tmp_path / "ctx")
    path = tmp_path / "ctx" / "tool_result_1.txt"
    text = store.preview("0123456789", path, head_chars=4)
    assert "0123" in text
    assert "…" in text
    assert str(path) in text
    assert "10 字符" in text
    assert "请读取该文件" in text


def test_preview_short_no_ellipsis(tmp_path):
    store = ResultStore(tmp_path / "ctx")
    path = tmp_path / "ctx" / "tool_result_1.txt"
    text = store.preview("abc", path, head_chars=4)
    assert "abc" in text and "…" not in text


def test_save_fails_when_base_dir_is_file(tmp_path):
    f = tmp_path / "notadir"
    f.write_text("占位")
    with pytest.raises(StoreError):
        ResultStore(f)
