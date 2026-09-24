"""read_file 读缓存测试（ch16 F5）：mtime 键控、命中/失效、实例隔离。"""

from mewcode.agents.context import ParentContext, clear, push
from mewcode.tools.files import FileReadCache, ReadFileTool


def _read(path) -> "ToolResult":
    return ReadFileTool().execute({"file_path": str(path)})


def test_cache_hit_returns_cached_content(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("版本一", encoding="utf-8")
    cache = FileReadCache()
    push(ParentContext(read_cache=cache))
    try:
        first = _read(f)
        # 磁盘内容被改，但 mtime 保持第一次读取时的旧值 → 命中旧缓存
        mtime = f.stat().st_mtime
        f.write_text("版本二", encoding="utf-8")
        import os

        os.utime(f, (mtime, mtime))
        second = _read(f)
        assert first.content == "版本一"
        assert second.content == "版本一"  # 命中缓存（mtime 未变）
    finally:
        clear()


def test_cache_miss_on_mtime_change(tmp_path):
    import os

    f = tmp_path / "b.txt"
    f.write_text("版本一", encoding="utf-8")
    cache = FileReadCache()
    push(ParentContext(read_cache=cache))
    try:
        assert _read(f).content == "版本一"
        f.write_text("版本二", encoding="utf-8")
        # 本机文件系统 mtime 精度约 1 秒：显式推进 mtime 模拟真实修改
        os.utime(f, (f.stat().st_mtime + 2, f.stat().st_mtime + 2))
        assert _read(f).content == "版本二"  # 失效重读
    finally:
        clear()


def test_no_context_reads_disk_directly(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("内容", encoding="utf-8")
    assert _read(f).content == "内容"  # 无上下文：直读（存量行为）


def test_cache_instances_isolated(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("内容", encoding="utf-8")
    cache_a = FileReadCache()
    cache_b = FileReadCache()
    push(ParentContext(read_cache=cache_a))
    try:
        _read(f)  # 写进 cache_a
    finally:
        clear()
    # cache_b 独立：无记录
    assert cache_b.get(str(f), f.stat().st_mtime) is None
    assert cache_a.get(str(f), f.stat().st_mtime) == "内容"


def test_limit_applied_on_cache_hit(tmp_path):
    f = tmp_path / "e.txt"
    f.write_text("1\n2\n3\n", encoding="utf-8")
    cache = FileReadCache()
    push(ParentContext(read_cache=cache))
    try:
        ReadFileTool().execute({"file_path": str(f)})  # 全文入缓存
        result = ReadFileTool().execute({"file_path": str(f), "limit": 2})
        assert result.content.startswith("1\n2")
        assert "已截断" in result.content
    finally:
        clear()
