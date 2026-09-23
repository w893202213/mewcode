"""内置命令单测（ch12 F7）：12 条 handler 用 FakeUi 验证接口调用与文案。"""

import pytest

from mewcode.commands import builtins
from mewcode.commands.builtins import _REVIEW_PROMPT, build_registry
from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.registry import CommandConflictError, CommandRegistry, CommandSpec
from mewcode.commands.ui import (
    MemoryInfo,
    PermissionInfo,
    ResumeItem,
    SessionInfo,
    StatusInfo,
    TokenStats,
    UiController,
)


class FakeUi(UiController):
    """记录调用的完整替身；查询结果可配置。"""

    def __init__(self) -> None:
        self.displayed: list[str] = []
        self.sent: list[str] = []
        self.entered_plan = 0
        self.exited_plan = 0
        self.cycled = 0
        self.cycle_label = "默认"
        self.cleared = 0
        self.compacted = 0
        self.compact_text = "已压缩上下文。"
        self.choice_result: int | None = 1
        self.choose_calls: list[tuple[str, list[str]]] = []
        self.listed: list[ResumeItem] = []
        self.restored: list[str] = []
        self.restore_text = "已恢复会话。"
        self.status = StatusInfo(
            config_name="cfg", model="model-x", thinking=True,
            permission_label="默认", plan_mode=False, tokens=TokenStats(),
        )
        self.session = SessionInfo(
            enabled=True, session_id="20260923-120000-abcd", title="你好",
            message_count=3, archive_path="/tmp/x.jsonl", last_activity="09-23 12:00",
        )
        self.memory = MemoryInfo(
            enabled=True, instruction_files=["MEWCODE.md  已加载"],
            project_notes=1, user_notes=2, session_files=3,
        )
        self.permission = PermissionInfo(
            mode_label="默认", plan_mode=False, session_rules=1, permanent_rules=2,
        )

    def display(self, text: str) -> None:
        self.displayed.append(text)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)

    def enter_plan_mode(self) -> None:
        self.entered_plan += 1
        self.status.plan_mode = True

    def exit_plan_mode(self) -> None:
        self.exited_plan += 1
        self.status.plan_mode = False

    def cycle_permission_mode(self) -> str:
        self.cycled += 1
        return self.cycle_label

    def refresh_status(self) -> None:
        pass

    def clear_conversation(self) -> None:
        self.cleared += 1

    def compact_now(self) -> str:
        self.compacted += 1
        return self.compact_text

    def choose(self, title: str, items: list[str]) -> int | None:
        self.choose_calls.append((title, items))
        return self.choice_result

    def status_info(self) -> StatusInfo:
        return self.status

    def session_info(self) -> SessionInfo:
        return self.session

    def memory_info(self) -> MemoryInfo:
        return self.memory

    def permission_info(self) -> PermissionInfo:
        return self.permission

    def list_sessions(self) -> list[ResumeItem]:
        return self.listed

    def restore_session(self, session_id: str) -> str:
        self.restored.append(session_id)
        return self.restore_text


@pytest.fixture()
def ui() -> FakeUi:
    return FakeUi()


@pytest.fixture()
def registry() -> CommandRegistry:
    return build_registry()


# ---------- 注册构建 ----------

def test_build_registry_has_12_commands():
    r = build_registry()
    assert len(r.all()) == 12
    assert r.validate() == []


def test_alias_table():
    r = build_registry()
    assert r.resolve("h").name == "help"
    assert r.resolve("q").name == "exit"
    assert r.resolve("quit").name == "exit"
    assert r.resolve("r").name == "review"
    assert r.resolve("m").name == "memory"
    assert r.resolve("mode").name == "permission"
    assert r.resolve("perm").name == "permission"
    assert r.resolve("s").name == "session"


def test_build_registry_raises_on_conflict(monkeypatch):
    def broken(registry: CommandRegistry) -> None:
        registry.register(CommandSpec(name="x", description=""))
        registry.register(CommandSpec(name="x", description=""))

    monkeypatch.setattr(builtins, "register_builtins", broken)
    with pytest.raises(CommandConflictError):
        builtins.build_registry()


# ---------- /help ----------

def test_help_lists_all_visible_commands(registry, ui):
    result = dispatch(registry, ParsedCommand(name="help", args=""), ui)
    assert result is CommandResult.CONTINUE
    text = ui.displayed[0]
    assert "可用命令" in text
    for name in ("help", "compact", "clear", "plan", "do", "session", "memory",
                 "permission", "status", "review", "exit", "resume"):
        assert f"/{name}" in text
    assert "别名 /mode、/perm" in text  # 别名展示


def test_help_detail(registry, ui):
    dispatch(registry, ParsedCommand(name="help", args="plan"), ui)
    text = ui.displayed[0]
    assert "用法: /plan" in text
    assert "类型: 影响界面状态" in text


def test_help_detail_by_alias(registry, ui):
    dispatch(registry, ParsedCommand(name="help", args="mode"), ui)
    assert "/permission —" in ui.displayed[0]
    assert "别名: /mode、/perm" in ui.displayed[0]


def test_help_unknown_command(registry, ui):
    dispatch(registry, ParsedCommand(name="help", args="nope"), ui)
    assert "未知命令" in ui.displayed[0] and "/help" in ui.displayed[0]


# ---------- 界面类 ----------

def test_clear(registry, ui):
    dispatch(registry, ParsedCommand(name="clear", args=""), ui)
    assert ui.cleared == 1
    assert "已清空" in ui.displayed[0]


def test_plan_enter_and_idempotent(registry, ui):
    dispatch(registry, ParsedCommand(name="plan", args=""), ui)
    assert ui.entered_plan == 1
    assert "已进入计划模式" in ui.displayed[0]
    dispatch(registry, ParsedCommand(name="plan", args=""), ui)
    assert ui.entered_plan == 1  # 已在计划模式：不再切换
    assert "已在计划模式" in ui.displayed[1]


def test_do_exit_and_idempotent(registry, ui):
    ui.status.plan_mode = True
    dispatch(registry, ParsedCommand(name="do", args=""), ui)
    assert ui.exited_plan == 1
    assert "已切回执行模式" in ui.displayed[0]
    dispatch(registry, ParsedCommand(name="do", args=""), ui)
    assert ui.exited_plan == 1
    assert "当前不在计划模式" in ui.displayed[1]


def test_compact(registry, ui):
    dispatch(registry, ParsedCommand(name="compact", args=""), ui)
    assert ui.compacted == 1
    assert ui.displayed[0] == ui.compact_text


def test_permission_cycles(registry, ui):
    dispatch(registry, ParsedCommand(name="permission", args=""), ui)
    assert ui.cycled == 1
    assert "已切换为「默认」" in ui.displayed[0]
    assert "再次输入 /permission" in ui.displayed[0]


def test_permission_show(registry, ui):
    dispatch(registry, ParsedCommand(name="permission", args="show"), ui)
    assert ui.cycled == 0
    text = ui.displayed[0]
    assert "权限模式: 默认" in text
    assert "规则: 会话 1 条 · 永久 2 条" in text


def test_mode_alias_cycles(registry, ui):
    dispatch(registry, ParsedCommand(name="mode", args=""), ui)
    assert ui.cycled == 1


def test_exit(registry, ui):
    result = dispatch(registry, ParsedCommand(name="exit", args=""), ui)
    assert result is CommandResult.EXIT
    assert "再见" in ui.displayed[0]


def test_q_alias_exits(registry, ui):
    result = dispatch(registry, ParsedCommand(name="q", args=""), ui)
    assert result is CommandResult.EXIT


# ---------- 本地类 ----------

def test_status_formatting(registry, ui):
    ui.status.tokens = TokenStats(
        round_in=10, round_out=20, round_cache_hit=5, round_cache_write=3,
        total_in=100, total_out=200, message_count=8, context_tokens=2048, window=100000,
    )
    dispatch(registry, ParsedCommand(name="status", args=""), ui)
    text = ui.displayed[0]
    assert "配置: cfg" in text
    assert "模型: model-x（思考开启）" in text
    assert "权限模式: 默认" in text
    assert "计划模式: 关闭" in text
    assert "输入 10 · 输出 20 · 缓存命中 5 · 缓存写入 3" in text
    assert "累计 token: 输入 100 · 输出 200" in text
    assert "消息 8 条 · 估算约 2048 token / 窗口上限 100000" in text


def test_status_without_cache_and_estimate(registry, ui):
    dispatch(registry, ParsedCommand(name="status", args=""), ui)
    text = ui.displayed[0]
    assert "缓存" not in text.split("本轮")[1].split("累计")[0]
    assert "消息 0 条" in text


def test_session_formatting(registry, ui):
    dispatch(registry, ParsedCommand(name="session", args=""), ui)
    text = ui.displayed[0]
    assert "会话 ID: 20260923-120000-abcd" in text
    assert "标题: 你好" in text
    assert "消息数: 3 条" in text
    assert "/tmp/x.jsonl" in text


def test_session_without_memory(registry, ui):
    ui.session.enabled = False
    dispatch(registry, ParsedCommand(name="session", args=""), ui)
    assert "纯内存会话" in ui.displayed[0]


def test_memory_formatting(registry, ui):
    dispatch(registry, ParsedCommand(name="memory", args=""), ui)
    text = ui.displayed[0]
    assert "MEWCODE.md  已加载" in text
    assert "项目 1 条 · 用户 2 条" in text
    assert "会话存档：3 个" in text


def test_memory_without_system(registry, ui):
    ui.memory.enabled = False
    dispatch(registry, ParsedCommand(name="memory", args=""), ui)
    assert "记忆系统未启用" in ui.displayed[0]


# ---------- 提示词类 ----------

def test_review_sends_preset_prompt(registry, ui):
    result = dispatch(registry, ParsedCommand(name="review", args=""), ui)
    assert result is CommandResult.CONTINUE
    assert ui.sent == [_REVIEW_PROMPT]
    assert "git diff" in ui.sent[0]


# ---------- /resume ----------

def test_resume_without_memory(registry, ui):
    ui.memory.enabled = False
    dispatch(registry, ParsedCommand(name="resume", args=""), ui)
    assert "记忆系统未启用" in ui.displayed[0]
    assert ui.choose_calls == []


def test_resume_no_sessions(registry, ui):
    dispatch(registry, ParsedCommand(name="resume", args=""), ui)
    assert "没有可恢复的会话" in ui.displayed[0]


def test_resume_flow(registry, ui):
    ui.listed = [
        ResumeItem(id="a", title="t1", message_count=1, label="L1"),
        ResumeItem(id="b", title="t2", message_count=2, label="L2"),
    ]
    dispatch(registry, ParsedCommand(name="resume", args=""), ui)
    assert ui.choose_calls == [("最近会话", ["L1", "L2"])]
    assert ui.restored == ["a"]
    assert ui.displayed[0] == ui.restore_text


def test_resume_cancel(registry, ui):
    ui.listed = [ResumeItem(id="a", title="t1", message_count=1, label="L1")]
    ui.choice_result = None
    dispatch(registry, ParsedCommand(name="resume", args=""), ui)
    assert ui.restored == []
    assert ui.displayed == []
