"""SkillManager 测试（ch13 F4/F5/F7）：激活、热更新、白名单、pending、clear。"""

from pathlib import Path

from mewcode.commands.registry import CommandRegistry, CommandSpec, CommandType
from mewcode.skills.manager import SkillManager
from mewcode.skills.model import parse_skill_file


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill_text(
    name="demo", description="说明", extra="", body="正文 $ARGS"
) -> str:
    return f"---\nname: {name}\ndescription: {description}{extra}\n---\n{body}\n"


def _make_skill(tmp_path, name="demo", tools=None, body="正文 $ARGS", dir=None):
    """在 tmp_path 写一个真实技能文件并返回解析后的 Skill（热更新重读依赖真文件）。"""
    path = tmp_path / f"{name}.md"
    extra = ""
    if tools is not None:
        extra = f"\ntools: [{', '.join(tools)}]"
    _write(path, _skill_text(name=name, extra=extra, body=body))
    skill, error = parse_skill_file(path, "builtin", dir=dir)
    assert error is None
    return skill


def _manager(tmp_path, registry=None):
    skills = {"demo": _make_skill(tmp_path)}
    manager = SkillManager(skills)
    if registry is not None:
        manager.attach_registry(registry)
    return manager


def test_activate_returns_substituted_body(tmp_path):
    manager = _manager(tmp_path)
    result = manager.activate("demo", "修复登录 bug")
    assert result.ok
    assert result.body == "正文 修复登录 bug"
    assert result.first_time is True
    assert result.skill.name == "demo"


def test_activate_unknown_skill(tmp_path):
    manager = _manager(tmp_path)
    result = manager.activate("nope")
    assert not result.ok
    assert "未知技能" in result.error


def test_reactivate_same_skill_idempotent(tmp_path):
    manager = _manager(tmp_path)
    manager.activate("demo", "参数一")
    result = manager.activate("demo", "参数二")
    assert result.ok
    assert result.first_time is False
    # 快照里只有一份，正文是第二次参数替换后的内容
    snapshot = manager.active_snapshot()
    assert snapshot.count("### demo") == 1
    assert "参数二" in snapshot


def test_hot_reload_rereads_disk(tmp_path):
    manager = _manager(tmp_path)
    manager.activate("demo", "x")
    assert "正文" in manager.active_snapshot()
    path = tmp_path / "demo.md"
    _write(path, _skill_text(body="第二版 $ARGS"))
    manager.activate("demo", "x")
    assert "第二版" in manager.active_snapshot()


def test_hot_reload_broken_file_reports_error(tmp_path):
    manager = _manager(tmp_path)
    manager.activate("demo")
    _write(tmp_path / "demo.md", "坏掉了")
    result = manager.activate("demo")
    assert not result.ok
    assert "重读失败" in result.error


def test_active_snapshot_multiple_skills(tmp_path):
    skills = {
        "a": _make_skill(tmp_path, "a", body="A 指令"),
        "b": _make_skill(tmp_path, "b", body="B 指令"),
    }
    manager = SkillManager(skills)
    assert manager.active_snapshot() == ""
    manager.activate("a")
    manager.activate("b")
    snapshot = manager.active_snapshot()
    assert snapshot.startswith("【已激活技能】")
    assert "### a" in snapshot and "A 指令" in snapshot
    assert "### b" in snapshot and "B 指令" in snapshot
    # 激活顺序：a 在 b 前
    assert snapshot.index("### a") < snapshot.index("### b")


def test_active_names_tracks_activation_and_clear(tmp_path):
    """ch14：active_names 只读反映激活状态（未激活空集 / 激活出现 / clear 清空）。"""
    manager = _manager(tmp_path)
    assert manager.active_names() == set()
    manager.activate("demo")
    assert manager.active_names() == {"demo"}
    manager.clear()
    assert manager.active_names() == set()


def test_active_names_multiple(tmp_path):
    skills = {
        "a": _make_skill(tmp_path, "a"),
        "b": _make_skill(tmp_path, "b"),
    }
    manager = SkillManager(skills)
    manager.activate("a")
    manager.activate("b")
    assert manager.active_names() == {"a", "b"}


def test_snapshot_includes_skill_dir(tmp_path):
    skill = _make_skill(tmp_path, "pack", dir=tmp_path / "pack")
    manager = SkillManager({"pack": skill})
    manager.activate("pack")
    assert f"技能目录: {tmp_path / 'pack'}" in manager.active_snapshot()


def test_whitelist_union_and_active(tmp_path):
    manager = SkillManager(
        {
            "a": _make_skill(tmp_path, "a", tools=["run_command", "read_file"]),
            "b": _make_skill(tmp_path, "b", tools=["read_file", "grep_code"]),
            "c": _make_skill(tmp_path, "c", tools=None),
        }
    )
    assert not manager.whitelist_active()
    manager.activate("a")
    assert manager.whitelist_active()
    assert manager.whitelist() == frozenset({"run_command", "read_file"})
    manager.activate("b")
    assert manager.whitelist() == frozenset({"run_command", "read_file", "grep_code"})
    # 无白名单的技能不影响收窄状态
    manager.clear()
    manager.activate("c")
    assert not manager.whitelist_active()
    assert manager.is_allowed("anything")


def test_is_allowed_execution_side(tmp_path):
    manager = SkillManager({"a": _make_skill(tmp_path, "a", tools=["run_command"])})
    assert manager.is_allowed("read_file")  # 未激活 → 不收窄
    manager.activate("a")
    assert manager.is_allowed("run_command")
    assert not manager.is_allowed("read_file")


def test_pending_single_slot(tmp_path):
    manager = _manager(tmp_path)
    assert manager.take_pending() is None
    manager.set_pending("demo", "参数")
    manager.set_pending("other", "参数2")  # 后写覆盖先写
    assert manager.take_pending() == ("other", "参数2")
    assert manager.take_pending() is None


def test_command_registered_on_first_activate(tmp_path):
    registry = CommandRegistry()
    registry.register(CommandSpec(name="help", description="内置", type=CommandType.LOCAL))
    manager = _manager(tmp_path, registry=registry)
    manager.activate("demo")
    assert registry.resolve("demo") is not None
    assert registry.resolve("demo").type is CommandType.PROMPT
    # 重复激活不重复注册（无冲突）
    manager.activate("demo")
    assert registry.validate() == []


def test_command_conflict_with_builtin_skipped(tmp_path):
    registry = CommandRegistry()
    registry.register(CommandSpec(name="demo", description="内置", type=CommandType.LOCAL))
    manager = _manager(tmp_path, registry=registry)
    manager.activate("demo")
    warnings = manager.take_warnings()
    assert len(warnings) == 1
    assert "撞名" in warnings[0]
    # 内置命令仍在，且注册表中只有一条 demo（技能命令未注册）
    assert len([s for s in registry.all() if s.name == "demo"]) == 1


def test_clear_unregisters_and_resets(tmp_path):
    registry = CommandRegistry()
    manager = _manager(tmp_path, registry=registry)
    manager.activate("demo")
    assert registry.resolve("demo") is not None
    manager.set_pending("demo", "x")
    manager.clear()
    assert registry.resolve("demo") is None
    assert manager.active_snapshot() == ""
    assert not manager.whitelist_active()
    assert manager.take_pending() is None
