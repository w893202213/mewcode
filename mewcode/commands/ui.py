"""界面控制接口：命令处理函数与界面之间的唯一桥梁（ch12）。

命令实现不直接持有 Console / session / Agent，也不 import 渲染框架；
所有界面副作用与状态查询都经 UiController 协议完成，TUI 层
（tui/uicontroller.py）提供实现。查询结果用纯数据类承载，
处理函数只做「参数判断 + 接口调用 + 文本格式化」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class TokenStats:
    """token 用量快照：最近一轮 + 会话累计 + 上下文占用估算。"""

    round_in: int = 0  # 最近一轮输入
    round_out: int = 0  # 最近一轮输出
    round_cache_hit: int | None = None  # 服务端未返回缓存数据时为 None
    round_cache_write: int | None = None
    total_in: int = 0  # 会话累计输入
    total_out: int = 0  # 会话累计输出
    message_count: int = 0  # 当前消息数
    context_tokens: int | None = None  # 上下文占用估算（字符折算，展示级）
    window: int = 0  # 上下文窗口上限


@dataclass
class StatusInfo:
    """/status 展示数据。"""

    config_name: str
    model: str
    thinking: bool
    permission_label: str  # 权限档中文标签（严格/默认/放行）
    plan_mode: bool
    tokens: TokenStats


@dataclass
class SessionInfo:
    """/session 展示数据；记忆系统未启用时 enabled=False，其余字段为空。"""

    enabled: bool
    session_id: str = ""
    title: str = ""
    message_count: int = 0
    archive_path: str = ""
    last_activity: str = ""


@dataclass
class MemoryInfo:
    """/memory 展示数据。"""

    enabled: bool
    instruction_files: list[str] = field(default_factory=list)  # 三层指令文件状态描述行
    project_notes: int = 0  # 项目级笔记数（.mewcode/memory/*.md）
    user_notes: int = 0  # 用户级笔记数（~/.mewcode/memory/*.md）
    session_files: int = 0  # 会话存档数（.mewcode/sessions/*.jsonl）


@dataclass
class PermissionInfo:
    """/permission show 展示数据。"""

    mode_label: str  # 权限档中文标签
    plan_mode: bool
    session_rules: int = 0  # 会话级规则数
    permanent_rules: int = 0  # 永久规则数（文件来源）


@dataclass
class ResumeItem:
    """/resume 列表项。"""

    id: str
    title: str
    message_count: int
    label: str  # 列表展示文本（标题 + ID + 消息数 + 时间）


class UiController(Protocol):
    """界面控制接口：命令处理函数唯一依赖的界面抽象（ch12 F4）。"""

    def display(self, text: str) -> None:
        """显示反馈文本。"""
        ...

    def send_user_message(self, text: str) -> None:
        """提示词类命令：把预设文本作为用户消息送入对话并触发 Agent 一轮。"""
        ...

    def run_skill(self, name: str, args: str) -> None:
        """执行技能（ch13 F7）：按技能当前模式分流——共享模式把 SOP 作为
        用户消息注入对话触发一轮；独立模式触发独立执行 + 摘要回流。"""
        ...

    def enter_plan_mode(self) -> None:
        """进入计划模式并刷新状态栏。"""
        ...

    def exit_plan_mode(self) -> None:
        """退出计划模式并刷新状态栏。"""
        ...

    def cycle_permission_mode(self) -> str:
        """循环切换权限档并刷新状态栏；返回新档位中文标签。"""
        ...

    def refresh_status(self) -> None:
        """刷新状态栏。"""
        ...

    def clear_conversation(self) -> None:
        """清空对话上下文。"""
        ...

    def compact_now(self) -> str:
        """立即压缩上下文；返回压缩反馈文本。"""
        ...

    def choose(self, title: str, items: list[str]) -> int | None:
        """多步交互：面板展示 items 并读入编号选择（1 起）；取消/EOF 返回 None。"""
        ...

    def status_info(self) -> StatusInfo: ...
    def session_info(self) -> SessionInfo: ...
    def memory_info(self) -> MemoryInfo: ...
    def permission_info(self) -> PermissionInfo: ...
    def list_sessions(self) -> list[ResumeItem]: ...
    def restore_session(self, session_id: str) -> str: ...
