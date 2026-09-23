"""技能斜杠命令测试（ch13 F7/AC12/AC13）：注册、撤销、撞名、分发路径。"""

from pathlib import Path

from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.registry import CommandRegistry, CommandSpec, CommandType
from mewcode.commands.ui import (
    MemoryInfo,
    PermissionInfo,
    ResumeItem,
    SessionInfo,
    StatusInfo,
    TokenStats,
    UiController,
)
from mewcode.skills.manager import SkillManager
from mewcode.skills.model import parse_skill_file


class FakeUi(UiController):
    """记录 run_skill 调用的最小替身。"""

    def __init__(self) -> None:
        self.displayed: list[str] = []
        self.sent: list[str] = []
        self.ran_skills: list[tuple[str, str]] = []

    def display(self, text: str) -> None:
        self.displayed.append(text)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)

    def run_skill(self, name: str, args: str) -> None:
        self.ran_skills.append((name, args))

    def enter_plan_mode(self) -> None: ...
    def exit_plan_mode(self) -> None: ...
    def cycle_permission_mode(self) -> str: return ""
    def refresh_status(self) -> None: ...
    def clear_conversation(self) -> None: ...
    def compact_now(self) -> str: return ""
    def choose(self, title, items): return None
    def status_info(self) -> StatusInfo: ...
    def session_info(self) -> SessionInfo: ...
    def memory_info(self) -> MemoryInfo: ...
    def permission_info(self) -> PermissionInfo: ...
    def list_sessions(self) -> list[ResumeItem]: ...
    def restore_session(self, session_id: str) -> str: ...


def _registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(CommandSpec(name="help", description="内置", type=CommandType.LOCAL))
    return registry


def _manager(tmp_path, name="demo") -> SkillManager:
    path = tmp_path / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: {name} 技能说明\n---\nSOP 正文\n",
        encoding="utf-8",
    )
    skill, error = parse_skill_file(path, "builtin")
    assert error is None
    return SkillManager({name: skill})


def test_skill_command_dispatches_to_run_skill(tmp_path):
    """AC12：技能命令经分发器触发 ui.run_skill，参数原样传入。"""
    registry = _registry()
    manager = _manager(tmp_path)
    manager.attach_registry(registry)
    manager.activate("demo")
    ui = FakeUi()
    result = dispatch(registry, ParsedCommand(name="demo", args=" 带空格参数 "), ui)
    assert result is CommandResult.CONTINUE
    assert ui.ran_skills == [("demo", " 带空格参数 ")]  # 参数原样传入 run_skill


def test_skill_command_visible_in_registry(tmp_path):
    registry = _registry()
    manager = _manager(tmp_path)
    manager.attach_registry(registry)
    manager.activate("demo")
    spec = registry.resolve("demo")
    assert spec is not None
    assert spec.type is CommandType.PROMPT
    assert spec.description == "demo 技能说明"  # /help 展示用
    # 可见列表（补全与 /help 数据源）包含它
    assert "demo" in [s.name for s in registry.visible()]


def test_clear_unregisters_skill_command(tmp_path):
    registry = _registry()
    manager = _manager(tmp_path)
    manager.attach_registry(registry)
    manager.activate("demo")
    assert registry.resolve("demo") is not None
    manager.clear()
    assert registry.resolve("demo") is None
    # 内置命令不受影响
    assert registry.resolve("help") is not None


def test_collision_with_builtin_skips_registration(tmp_path):
    """AC13：技能名撞内置命令 → 跳过注册并警告，内置命令优先。"""
    registry = _registry()
    manager = _manager(tmp_path, name="help")
    manager.attach_registry(registry)
    manager.activate("help")
    assert manager.take_warnings() == ["技能命令 /help 与内置命令撞名，跳过注册"]
    # 注册表里仍只有内置那条
    specs = [s for s in registry.all() if s.name == "help"]
    assert len(specs) == 1
    assert specs[0].handler is None  # 内置 help 无 handler（动态生成），技能未覆盖它


def test_activate_without_registry_no_command(tmp_path):
    """未 attach registry 时激活不注册命令（独立对话的内层 manager 场景）。"""
    manager = _manager(tmp_path)
    result = manager.activate("demo")
    assert result.ok
    assert manager.take_warnings() == []


def test_unknown_skill_command_not_registered(tmp_path):
    registry = _registry()
    manager = _manager(tmp_path)
    manager.attach_registry(registry)
    result = manager.activate("nope")
    assert not result.ok
    assert registry.resolve("nope") is None


# ---------- ch14: 内置 /skill 命令 ----------

from mewcode.commands.builtins import register_skill_command


def test_register_skill_command_metadata(tmp_path):
    """ch14 F4：/skill 注册为 LOCAL 内置命令，进可见列表（/help 与补全数据源）。"""
    registry = _registry()
    manager = _manager(tmp_path)
    register_skill_command(registry, manager)
    spec = registry.resolve("skill")
    assert spec is not None
    assert spec.type is CommandType.LOCAL
    assert spec.usage == "/skill"
    assert "skill" in [s.name for s in registry.visible()]


def test_skill_named_skill_activation_keeps_builtin(tmp_path):
    """ch14 AC6：技能名撞 /skill → 短命令跳过注册并警告，内置 /skill 不受影响。"""
    registry = _registry()
    manager = _manager(tmp_path, name="skill")
    register_skill_command(registry, manager)
    manager.attach_registry(registry)
    manager.activate("skill")
    assert manager.take_warnings() == ["技能命令 /skill 与内置命令撞名，跳过注册"]
    # 注册表里只有内置 /skill 一条（技能未覆盖它）
    specs = [s for s in registry.all() if s.name == "skill"]
    assert len(specs) == 1
    assert specs[0].type is CommandType.LOCAL


def test_skill_command_displays_list_via_handler(tmp_path):
    """ch14 F1：dispatch /skill → ui.display 收到技能列表文本（激活状态实时）。"""
    registry = _registry()
    manager = _manager(tmp_path)
    register_skill_command(registry, manager)
    manager.activate("demo")
    ui = FakeUi()
    result = dispatch(registry, ParsedCommand(name="skill", args=""), ui)
    assert result is CommandResult.CONTINUE
    assert ui.displayed == [
        "可用技能：\n  ★ demo         demo 技能说明\n共 1 个技能，1 个已激活。"
    ]
