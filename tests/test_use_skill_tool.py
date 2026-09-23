"""UseSkillTool 测试（ch13 F3/F6/F8）：shared 返回 SOP、independent 登记 pending。"""

from pathlib import Path

from mewcode.skills.manager import SkillManager
from mewcode.skills.model import parse_skill_file
from mewcode.skills.use_skill import UseSkillTool
from mewcode.tools.base import run_tool


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _manager_with(tmp_path, name, extra="", body="SOP 指令 $ARGS"):
    path = tmp_path / f"{name}.md"
    _write(
        path,
        f"---\nname: {name}\ndescription: 说明{extra}\n---\n{body}\n",
    )
    skill, error = parse_skill_file(path, "builtin")
    assert error is None
    return SkillManager({name: skill}), path


def test_shared_returns_full_sop(tmp_path):
    manager, _ = _manager_with(tmp_path, "review", body="审查步骤 $ARGS")
    tool = UseSkillTool(manager)
    result = run_tool(tool, {"name": "review", "args": "改动集"})
    assert result.ok
    assert "已激活" in result.content
    assert "审查步骤 改动集" in result.content  # 参数已替换
    assert "review" in manager.active_snapshot()  # 同时钉入环境上下文
    assert manager.take_pending() is None  # shared 不登记 pending


def test_independent_sets_pending(tmp_path):
    manager, _ = _manager_with(tmp_path, "commit", extra="\nmode: independent")
    tool = UseSkillTool(manager)
    result = run_tool(tool, {"name": "commit", "args": "修复登录 bug"})
    assert result.ok
    assert "本轮结束后" in result.content
    assert manager.take_pending() == ("commit", "修复登录 bug")
    assert manager.take_pending() is None  # 单槽取走即空


def test_unknown_skill_fails(tmp_path):
    manager, _ = _manager_with(tmp_path, "demo")
    tool = UseSkillTool(manager)
    result = run_tool(tool, {"name": "nope"})
    assert not result.ok
    assert "未知技能" in result.content


def test_args_optional_defaults_empty(tmp_path):
    manager, _ = _manager_with(tmp_path, "demo", body="内容[$ARGS]")
    tool = UseSkillTool(manager)
    result = run_tool(tool, {"name": "demo"})
    assert result.ok
    assert "内容[]" in result.content


def test_system_level_attrs():
    manager = SkillManager({})
    tool = UseSkillTool(manager)
    assert tool.name == "use_skill"
    assert tool.read_only is True  # 计划模式可见
    assert "name" in tool.input_schema["required"]
