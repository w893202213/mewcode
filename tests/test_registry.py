"""注册中心测试。"""

import pytest

from mewcode.tools.base import Tool, ToolError, ToolResult
from mewcode.tools.registry import ToolRegistry


class _DummyTool(Tool):
    name = "dummy"
    description = "测试工具"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("ok")


class _DummyTool2(Tool):
    name = "dummy2"
    description = "测试工具2"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("ok")


def test_register_and_get():
    r = ToolRegistry()
    r.register(_DummyTool())
    assert r.get("dummy").name == "dummy"


def test_get_unknown_raises_chinese_error():
    r = ToolRegistry()
    r.register(_DummyTool())
    with pytest.raises(ToolError) as ei:
        r.get("nope")
    assert "未知工具" in str(ei.value)
    assert "dummy" in str(ei.value)  # 列出已注册工具


def test_duplicate_name_raises():
    r = ToolRegistry()
    r.register(_DummyTool())
    with pytest.raises(ToolError) as ei:
        r.register(_DummyTool())
    assert "重名" in str(ei.value)


def test_list_sorted():
    r = ToolRegistry()
    r.register(_DummyTool2())
    r.register(_DummyTool())
    assert [t.name for t in r.list()] == ["dummy", "dummy2"]
