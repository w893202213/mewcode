"""斜杠命令：解析与分发。"""

from __future__ import annotations

from enum import Enum, auto

from mewcode.agent.loop import Agent
from mewcode.session import ChatSession


class CommandResult(Enum):
    """命令执行结果：继续主循环还是退出。"""

    CONTINUE = auto()
    EXIT = auto()


# 可用命令列表（供补全与 /help 展示）
COMMAND_NAMES = ["/exit", "/clear", "/help", "/plan", "/do", "/mode", "/compact", "/resume"]

_HELP_TEXT = "\n".join(
    [
        "可用命令：",
        "  /exit     退出 MewCode",
        "  /clear    清空对话上下文，重新开始",
        "  /help     显示本帮助",
        "  /plan     进入计划模式：仅只读工具可用，模型先调研并给出计划",
        "  /do       退出计划模式：切回全部工具，按计划执行",
        "  /mode     循环切换权限档位：严格 → 默认 → 放行",
        "  /compact  压缩对话上下文：较早的消息生成摘要，近期消息原文保留",
        "  /resume   列出最近会话并恢复其中一个",
        "输入历史可用上下方向键回溯。",
    ]
)


def handle_command(
    text: str, session: ChatSession, agent: Agent
) -> tuple[CommandResult, str]:
    """处理斜杠命令，返回（结果，反馈文本）。

    反馈文本由 TUI 显示，可能是命令说明、清空提示或未知命令提示。
    """
    command = text.strip()
    if command == "/exit":
        return CommandResult.EXIT, "再见！👋"
    if command == "/clear":
        session.clear()
        return CommandResult.CONTINUE, "已清空对话上下文，重新开始。"
    if command == "/help":
        return CommandResult.CONTINUE, _HELP_TEXT
    if command == "/plan":
        if agent.plan_mode:
            return CommandResult.CONTINUE, "已在计划模式（仅只读工具可用，输入 /do 切回执行模式）。"
        agent.enter_plan_mode()
        return CommandResult.CONTINUE, (
            "已进入计划模式：仅只读工具可用，模型会先调研并给出计划。"
            "审阅满意后输入 /do 切回执行模式。"
        )
    if command == "/do":
        if agent.plan_mode:
            agent.exit_plan_mode()
            return CommandResult.CONTINUE, "已切回执行模式，可以按计划动手了。"
        return CommandResult.CONTINUE, "当前不在计划模式。"
    if command == "/mode":
        from mewcode.permissions import MODE_LABELS

        new_mode = agent.cycle_permission_mode()
        return CommandResult.CONTINUE, (
            f"权限模式已切换为「{MODE_LABELS[new_mode]}」（{new_mode.value}）。"
            "再次输入 /mode 切换下一档。"
        )
    if command == "/compact":
        # ch09 F5：无条件执行重量兜底压缩（同步，摘要期间输入阻塞可接受）
        return CommandResult.CONTINUE, agent.compact_now()
    if command == "/resume":
        # 实际多步交互由 tui/app.py 在主循环拦截处理（ch11 F3），
        # 此分支仅为防御：走到这里说明界面层没有拦截
        return CommandResult.CONTINUE, "请使用 /resume 选择要恢复的会话（由界面处理）。"
    return CommandResult.CONTINUE, f"未知命令: {command}（输入 /help 查看可用命令）"
