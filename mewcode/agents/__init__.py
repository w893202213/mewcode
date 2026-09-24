"""SubAgent 系统：主 Agent 委派子任务的独立执行单元（ch16）。

「定义式」按预定义角色从空白对话执行，「Fork 式」继承父对话历史借
prompt cache 降成本；子 Agent 跑到底非交互运行，结果异步通知回主对话。

注意：本包存在跨包加载环（tools.base → agents.context → 本包；
本包 → manager → loop → provider → tools.base），因此 __init__ 不 eager
导入子模块——公开名字经 PEP 562 惰性导出（from mewcode.agents import
TaskManager 仍可用）；内部调用方请直接用完整子模块路径。
"""

from __future__ import annotations

import importlib

__all__ = [
    "AgentRole",
    "DiscoverResult",
    "discover_agents",
    "AgentTask",
    "TaskManager",
    "TaskStatus",
]

# 名字 → (模块路径, 属性名)：首次访问时才加载对应子模块
_LAZY: dict[str, tuple[str, str]] = {
    "AgentRole": ("mewcode.agents.model", "AgentRole"),
    "DiscoverResult": ("mewcode.agents.discover", "DiscoverResult"),
    "discover_agents": ("mewcode.agents.discover", "discover_agents"),
    "AgentTask": ("mewcode.agents.manager", "AgentTask"),
    "TaskManager": ("mewcode.agents.manager", "TaskManager"),
    "TaskStatus": ("mewcode.agents.manager", "TaskStatus"),
}


def __getattr__(name: str):
    """惰性导出：避免包加载期的跨包循环导入。"""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module 'mewcode.agents' has no attribute {name!r}")
    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value  # 缓存，后续访问走正常属性路径
    return value
