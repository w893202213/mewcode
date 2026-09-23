"""斜杠命令系统：注册中心、解析器、界面控制接口、分发器与内置命令（ch12）。"""

from mewcode.commands.builtins import build_registry
from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand, parse_command
from mewcode.commands.registry import (
    CommandConflictError,
    CommandRegistry,
    CommandSpec,
    CommandType,
)
from mewcode.commands.ui import (
    MemoryInfo,
    PermissionInfo,
    ResumeItem,
    SessionInfo,
    StatusInfo,
    TokenStats,
    UiController,
)

__all__ = [
    "build_registry",
    "CommandConflictError",
    "CommandRegistry",
    "CommandResult",
    "CommandSpec",
    "CommandType",
    "dispatch",
    "MemoryInfo",
    "ParsedCommand",
    "parse_command",
    "PermissionInfo",
    "ResumeItem",
    "SessionInfo",
    "StatusInfo",
    "TokenStats",
    "UiController",
]
