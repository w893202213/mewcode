"""内置命令：12 条高频命令的元数据、处理函数与注册构建（ch12 F7）。

处理函数只依赖 (ParsedCommand, UiController)，界面副作用全部经接口；
/help 需要注册中心数据，经闭包捕获。文案格式化提为纯函数，可独立单测。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from mewcode.commands.dispatch import CommandResult
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.registry import (
    CommandConflictError,
    CommandRegistry,
    CommandSpec,
    CommandType,
)
from mewcode.commands.ui import (
    MemoryInfo,
    PermissionInfo,
    SessionInfo,
    StatusInfo,
    TokenStats,
    UiController,
)
from mewcode.skills.model import Skill

if TYPE_CHECKING:
    from mewcode.skills.manager import SkillManager

_TYPE_LABELS = {
    CommandType.LOCAL: "纯本地",
    CommandType.UI: "影响界面状态",
    CommandType.PROMPT: "预设提示词",
}


# ---------- 处理函数 ----------

def _cmd_clear(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.clear_conversation()
    ui.display("已清空对话上下文，重新开始。")
    return None


def _cmd_plan(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    if ui.status_info().plan_mode:
        ui.display("已在计划模式（仅只读工具可用，输入 /do 切回执行模式）。")
    else:
        ui.enter_plan_mode()
        ui.display(
            "已进入计划模式：仅只读工具可用，模型会先调研并给出计划。"
            "审阅满意后输入 /do 切回执行模式。"
        )
    return None


def _cmd_do(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    if ui.status_info().plan_mode:
        ui.exit_plan_mode()
        ui.display("已切回执行模式，可以按计划动手了。")
    else:
        ui.display("当前不在计划模式。")
    return None


def _cmd_compact(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.display(ui.compact_now())
    return None


def _cmd_permission(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    if cmd.args.strip() == "show":
        ui.display(_format_permission(ui.permission_info()))
    else:
        label = ui.cycle_permission_mode()
        ui.display(f"权限模式已切换为「{label}」。再次输入 /permission 切换下一档。")
    return None


def _cmd_status(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.display(_format_status(ui.status_info()))
    return None


def _cmd_session(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.display(_format_session(ui.session_info()))
    return None


def _cmd_memory(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.display(_format_memory(ui.memory_info()))
    return None


def _cmd_exit(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    ui.display("再见！👋")
    return CommandResult.EXIT


def _cmd_resume(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
    if not ui.memory_info().enabled:
        ui.display("记忆系统未启用，无法恢复会话。")
        return None
    items = ui.list_sessions()
    if not items:
        ui.display("没有可恢复的会话。")
        return None
    idx = ui.choose("最近会话", [item.label for item in items])
    if idx is None:
        return None
    ui.display(ui.restore_session(items[idx - 1].id))
    return None


# ---------- 文案格式化（纯函数） ----------

def render_skill_list(skills: dict[str, Skill], active: set[str]) -> str:
    """技能列表文本（ch14 F1）：按名排序，激活技能行首 ★，有激活时附统计行。"""
    if not skills:
        return "没有可用技能。"
    lines = ["可用技能："]
    for name in sorted(skills):
        mark = "★ " if name in active else "  "
        lines.append(f"  {mark}{name:<12} {skills[name].description}")
    n_active = len(active & set(skills))
    if n_active:
        lines.append(f"共 {len(skills)} 个技能，{n_active} 个已激活。")
    return "\n".join(lines)


def _render_help_list(specs: list[CommandSpec]) -> str:
    """全部可见命令列表（名称 + 描述 + 别名）。"""
    lines = ["可用命令："]
    for s in specs:
        alias_note = f"（别名 /{'、/'.join(s.aliases)}）" if s.aliases else ""
        lines.append(f"  /{s.name:<12} {s.description}{alias_note}")
    lines.append("输入 /help <命令> 查看单条命令详情。")
    return "\n".join(lines)


def _render_help_detail(spec: CommandSpec) -> str:
    """单条命令详情（描述、用法、别名、类型）。"""
    lines = [f"/{spec.name} — {spec.description}", f"用法: {spec.usage}"]
    if spec.aliases:
        lines.append(f"别名: /{'、/'.join(spec.aliases)}")
    lines.append(f"类型: {_TYPE_LABELS[spec.type]}")
    return "\n".join(lines)


def _format_status(info: StatusInfo) -> str:
    t = info.tokens
    lines = [
        f"配置: {info.config_name}",
        f"模型: {info.model}（思考{'开启' if info.thinking else '关闭'}）",
        f"权限模式: {info.permission_label}",
        f"计划模式: {'开启' if info.plan_mode else '关闭'}",
        "本轮 token: " + _token_round_line(t),
        f"累计 token: 输入 {t.total_in} · 输出 {t.total_out}",
        "上下文占用: " + _context_line(t),
    ]
    return "\n".join(lines)


def _token_round_line(t: TokenStats) -> str:
    """本轮 token 行：缓存字段服务端返回才展示。"""
    parts = [f"输入 {t.round_in}", f"输出 {t.round_out}"]
    if t.round_cache_hit is not None:
        parts.append(f"缓存命中 {t.round_cache_hit}")
    if t.round_cache_write is not None:
        parts.append(f"缓存写入 {t.round_cache_write}")
    return " · ".join(parts)


def _context_line(t: TokenStats) -> str:
    """上下文占用行：估算 token 与窗口上限（缺失时省略对应部分）。"""
    if t.context_tokens is None:
        return f"消息 {t.message_count} 条"
    line = f"消息 {t.message_count} 条 · 估算约 {t.context_tokens} token"
    if t.window:
        line += f" / 窗口上限 {t.window}"
    return line


def _format_session(info: SessionInfo) -> str:
    if not info.enabled:
        return "纯内存会话（无存档）。"
    lines = [
        f"会话 ID: {info.session_id}",
        f"标题: {info.title or '（无标题）'}",
        f"消息数: {info.message_count} 条",
        f"存档路径: {info.archive_path}",
    ]
    if info.last_activity:
        lines.append(f"最后活动: {info.last_activity}")
    return "\n".join(lines)


def _format_memory(info: MemoryInfo) -> str:
    if not info.enabled:
        return "记忆系统未启用。"
    lines = ["指令文件："] + [f"  {line}" for line in info.instruction_files]
    lines.append(f"笔记：项目 {info.project_notes} 条 · 用户 {info.user_notes} 条")
    lines.append(f"会话存档：{info.session_files} 个")
    return "\n".join(lines)


def _format_permission(info: PermissionInfo) -> str:
    return "\n".join(
        [
            f"权限模式: {info.mode_label}",
            f"计划模式: {'开启' if info.plan_mode else '关闭'}",
            f"规则: 会话 {info.session_rules} 条 · 永久 {info.permanent_rules} 条",
        ]
    )


# ---------- 注册构建 ----------

def make_skill_handler(manager: "SkillManager") -> Callable[
    [ParsedCommand, UiController], CommandResult | None
]:
    """/skill 命令处理器（ch14）：闭包捕获技能管理器，现取技能表与激活状态。

    与 /help 闭包捕获注册中心同模式；每次执行时现取，激活/清空后天然实时。
    """

    def cmd_skill(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
        ui.display(render_skill_list(manager.skills, manager.active_names()))
        return None

    return cmd_skill


def register_skill_command(
    registry: CommandRegistry, manager: "SkillManager"
) -> None:
    """登记 /skill 命令（ch14 F4）；须技能管理器创建后调用（cli 装配层）。"""
    registry.register(
        CommandSpec(
            name="skill",
            description="列出全部可用技能与激活状态",
            usage="/skill",
            type=CommandType.LOCAL,
            handler=make_skill_handler(manager),
        )
    )


def register_builtins(registry: CommandRegistry) -> None:
    """登记 12 条内置命令；/help 经闭包捕获注册中心动态渲染。"""

    def cmd_help(cmd: ParsedCommand, ui: UiController) -> CommandResult | None:
        target = cmd.args.strip()
        if not target:
            ui.display(_render_help_list(registry.visible()))
            return None
        spec = registry.resolve(target)
        if spec is None:
            ui.display(f"未知命令: /{target}（输入 /help 查看可用命令）")
        else:
            ui.display(_render_help_detail(spec))
        return None

    registry.register(
        CommandSpec(
            name="help", description="显示帮助与命令列表", usage="/help [命令]",
            aliases=("h",), arg_hint="[命令]", type=CommandType.LOCAL, handler=cmd_help,
        )
    )
    registry.register(
        CommandSpec(
            name="compact", description="立即压缩对话上下文", usage="/compact",
            type=CommandType.UI, handler=_cmd_compact,
        )
    )
    registry.register(
        CommandSpec(
            name="clear", description="清空对话上下文，重新开始", usage="/clear",
            type=CommandType.UI, handler=_cmd_clear,
        )
    )
    registry.register(
        CommandSpec(
            name="plan", description="进入计划模式（仅只读工具可用）", usage="/plan",
            type=CommandType.UI, handler=_cmd_plan,
        )
    )
    registry.register(
        CommandSpec(
            name="do", description="退出计划模式，切回执行", usage="/do",
            type=CommandType.UI, handler=_cmd_do,
        )
    )
    registry.register(
        CommandSpec(
            name="session", description="查看当前会话信息", usage="/session",
            aliases=("s",), type=CommandType.LOCAL, handler=_cmd_session,
        )
    )
    registry.register(
        CommandSpec(
            name="memory", description="查看记忆系统状态", usage="/memory",
            aliases=("m",), type=CommandType.LOCAL, handler=_cmd_memory,
        )
    )
    registry.register(
        CommandSpec(
            name="permission", description="循环切换权限档（show 查看详情）",
            usage="/permission [show]", aliases=("mode", "perm"), arg_hint="[show]",
            type=CommandType.UI, handler=_cmd_permission,
        )
    )
    registry.register(
        CommandSpec(
            name="status", description="查看系统状态与 token 用量", usage="/status",
            type=CommandType.LOCAL, handler=_cmd_status,
        )
    )
    registry.register(
        CommandSpec(
            name="exit", description="退出 MewCode", usage="/exit",
            aliases=("q", "quit"), type=CommandType.UI, handler=_cmd_exit,
        )
    )
    registry.register(
        CommandSpec(
            name="resume", description="列出最近会话并恢复", usage="/resume",
            type=CommandType.UI, handler=_cmd_resume,
        )
    )


def build_registry() -> CommandRegistry:
    """构建内置命令注册中心；冲突即抛异常（cli 层 stderr + exit(1)）。"""
    registry = CommandRegistry()
    register_builtins(registry)
    problems = registry.validate()
    if problems:
        raise CommandConflictError(problems)
    return registry
