"""Hook 系统：在 Agent 生命周期关键节点上挂自动化动作（ch15）。

「事件 + 条件 + 动作」三要素声明式规则：10 个生命周期事件、四种匹配
模式的条件、四种动作（命令/注入提示词/HTTP/子 Agent 占位）。规则从
config.yaml 顶层 `hooks:` 加载并集中校验，运行时由 HookManager 在
Agent 主循环各节点触发。
"""

from mewcode.hooks.loader import HookRule, load_hooks
from mewcode.hooks.manager import HookDecision, HookManager

__all__ = ["HookManager", "HookRule", "HookDecision", "load_hooks"]
