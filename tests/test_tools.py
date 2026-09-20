"""工具系统测试：run_tool 执行包装与六个核心工具。"""

import time
from pathlib import Path

import pytest

from mewcode.tools.base import RESULT_MAX_CHARS, Tool, ToolResult, run_tool
from mewcode.tools.files import EditFileTool, ReadFileTool, WriteFileTool
from mewcode.tools.search import FindFilesTool, GrepCodeTool
from mewcode.tools.shell import RunCommandTool

# ---------------------------------------------------------------------------
# run_tool 执行包装（T2）
# ---------------------------------------------------------------------------


class _SleepTool(Tool):
    name = "sleep_tool"
    description = "测试用：睡一段时间"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        time.sleep(1.0)
        return ToolResult.success("不该到达")


class _RaisingTool(Tool):
    name = "raising_tool"
    description = "测试用：抛异常"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        raise ValueError("参数不对")


class _LongTool(Tool):
    name = "long_tool"
    description = "测试用：超长结果"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("x" * (RESULT_MAX_CHARS + 100))


def test_run_tool_timeout():
    tool = _SleepTool()
    tool.timeout = 0.1  # 测试注入短超时
    result = run_tool(tool, {})
    assert not result.ok
    assert "超时" in result.content


def test_run_tool_exception_to_error():
    result = run_tool(_RaisingTool(), {})
    assert not result.ok
    assert "工具执行异常" in result.content
    assert "参数不对" in result.content


def test_run_tool_truncates_long_content():
    result = run_tool(_LongTool(), {})
    assert result.ok
    assert "已截断" in result.content
    assert len(result.content) <= RESULT_MAX_CHARS + 100


def test_run_tool_passes_success():
    class OkTool(Tool):
        name = "ok_tool"
        description = "d"
        input_schema = {"type": "object", "properties": {}}

        def execute(self, args: dict) -> ToolResult:
            return ToolResult.success("正常结果")

    result = run_tool(OkTool(), {})
    assert result.ok
    assert result.content == "正常结果"


# ---------------------------------------------------------------------------
# 文件工具（T4）
# ---------------------------------------------------------------------------


def test_read_file(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = run_tool(ReadFileTool(), {"file_path": str(f)})
    assert result.ok
    assert result.content == "line1\nline2\nline3\n"


def test_read_file_with_limit(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("1\n2\n3\n", encoding="utf-8")
    result = run_tool(ReadFileTool(), {"file_path": str(f), "limit": 2})
    assert result.ok
    assert result.content.startswith("1\n2")
    assert "已截断" in result.content


def test_read_file_missing(tmp_path: Path):
    result = run_tool(ReadFileTool(), {"file_path": str(tmp_path / "nope.py")})
    assert not result.ok
    assert "文件不存在" in result.content


def test_write_file_creates_and_overwrites(tmp_path: Path):
    f = tmp_path / "sub" / "b.txt"
    result = run_tool(WriteFileTool(), {"file_path": str(f), "content": "你好"})
    assert result.ok
    assert f.read_text(encoding="utf-8") == "你好"
    # 覆盖写
    result = run_tool(WriteFileTool(), {"file_path": str(f), "content": "再见"})
    assert result.ok
    assert f.read_text(encoding="utf-8") == "再见"


def test_edit_file_unique_match(tmp_path: Path):
    f = tmp_path / "c.py"
    f.write_text("x = 1\ny = 2\n", encoding="utf-8")
    result = run_tool(EditFileTool(), {"file_path": str(f), "old_text": "y = 2", "new_text": "y = 3"})
    assert result.ok
    assert "行" in result.content
    assert f.read_text(encoding="utf-8") == "x = 1\ny = 3\n"


def test_edit_file_zero_match(tmp_path: Path):
    f = tmp_path / "c.py"
    f.write_text("x = 1\n", encoding="utf-8")
    result = run_tool(
        EditFileTool(), {"file_path": str(f), "old_text": "不存在的内容", "new_text": "z"}
    )
    assert not result.ok
    assert "未找到" in result.content
    assert f.read_text(encoding="utf-8") == "x = 1\n"  # 文件未被改动


def test_edit_file_multiple_match(tmp_path: Path):
    f = tmp_path / "c.py"
    f.write_text("x = 1\ny = 1\n", encoding="utf-8")
    result = run_tool(EditFileTool(), {"file_path": str(f), "old_text": "= 1", "new_text": "= 2"})
    assert not result.ok
    assert "2 次" in result.content
    assert "行" in result.content
    assert f.read_text(encoding="utf-8") == "x = 1\ny = 1\n"  # 文件未被改动


def test_edit_file_empty_old_text(tmp_path: Path):
    f = tmp_path / "c.py"
    f.write_text("x\n", encoding="utf-8")
    result = run_tool(EditFileTool(), {"file_path": str(f), "old_text": "", "new_text": "y"})
    assert not result.ok


# ---------------------------------------------------------------------------
# 搜索工具（T5）
# ---------------------------------------------------------------------------


def test_find_files_glob(tmp_path: Path):
    (tmp_path / "a.py").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("", encoding="utf-8")
    result = run_tool(FindFilesTool(), {"pattern": "**/*.py", "path": str(tmp_path)})
    assert result.ok
    lines = result.content.strip().splitlines()
    assert len(lines) == 2
    assert any(l.endswith("a.py") for l in lines)
    assert any(l.endswith("c.py") for l in lines)


def test_find_files_no_match(tmp_path: Path):
    result = run_tool(FindFilesTool(), {"pattern": "*.rs", "path": str(tmp_path)})
    assert result.ok
    assert "未找到" in result.content


def test_grep_code_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("hello world\n", encoding="utf-8")
    result = run_tool(GrepCodeTool(), {"pattern": "hello", "path": str(tmp_path)})
    assert result.ok
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert ":1:" in result.content  # 带行号


def test_grep_code_case_sensitive(tmp_path: Path):
    (tmp_path / "a.py").write_text("Hello\n", encoding="utf-8")
    sensitive = run_tool(GrepCodeTool(), {"pattern": "hello", "path": str(tmp_path)})
    assert sensitive.ok
    assert "未找到" in sensitive.content or "Hello" not in sensitive.content
    insensitive = run_tool(
        GrepCodeTool(), {"pattern": "hello", "path": str(tmp_path), "case_sensitive": False}
    )
    assert insensitive.ok
    assert "a.py" in insensitive.content


def test_grep_code_bad_regex(tmp_path: Path):
    result = run_tool(GrepCodeTool(), {"pattern": "([", "path": str(tmp_path)})
    assert not result.ok
    assert "正则" in result.content


# ---------------------------------------------------------------------------
# 命令执行工具（T6）
# ---------------------------------------------------------------------------


def test_run_command_success():
    result = run_tool(RunCommandTool(), {"command": "echo hello"})
    assert result.ok
    assert "hello" in result.content


def test_run_command_nonzero_exit():
    result = run_tool(RunCommandTool(), {"command": "exit 3"})
    assert not result.ok
    assert "3" in result.content  # 退出码说明


def test_run_command_timeout():
    old_timeout = RunCommandTool.timeout
    RunCommandTool.timeout = 1.0  # 测试注入短超时
    try:
        result = run_tool(RunCommandTool(), {"command": "sleep 5"})
    finally:
        RunCommandTool.timeout = old_timeout
    assert not result.ok
    assert "超时" in result.content


def test_run_command_requires_confirmation():
    assert RunCommandTool.requires_confirmation is True
    assert ReadFileTool.requires_confirmation is False


def test_tool_read_only_markers():
    """只读标记：读三个 True；写/改/命令三个 False（plan 模式过滤 + 分批并发判定共用）。"""
    from mewcode.tools import create_registry

    registry = create_registry()
    read_only = [t.name for t in registry.list() if t.read_only]
    assert read_only == ["find_files", "grep_code", "read_file"]
    assert registry.get("write_file").read_only is False
    assert registry.get("edit_file").read_only is False
    assert registry.get("run_command").read_only is False
