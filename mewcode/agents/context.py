"""父上下文传递载体（ch16）：contextvars 让工具与 Hook 动作在执行
路径上拿到父 Agent、任务管理器与读缓存，不改任何工具接口签名。

为何是 contextvars 而非 threading.local：Hook 动作在 manager 内经
asyncio.to_thread 执行，to_thread 会把调用方 context 复制进新线程——
threading.local 不跨线程（e2e 审查发现的断链缺陷：hook subagent 动作
在生产路径永远取不到 TaskManager）。loop.py 的 _run_call（工具执行）
与 _emit_hook（Hook 触发）在各自路径压入 context；run_tool 的裸守护
线程不复制 context，由它手动捕获并在执行线程内重设。

类型用 Any 避免与 loop.py / manager.py 的循环依赖（运行时对象引用）。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Any


@dataclass
class ParentContext:
    """一段执行路径内的父上下文快照。"""

    agent: Any = None  # 父 Agent 引用（Fork 取消息/可见工具/档位/stable 参数）
    task_manager: Any = None  # TaskManager（agent 工具与 hook subagent 动作用）
    read_cache: Any = None  # FileReadCache 实例（read_file 缓存隔离载体）


_var: contextvars.ContextVar[ParentContext | None] = contextvars.ContextVar(
    "mewcode_parent_ctx", default=None
)


def push(ctx: ParentContext):
    """压入当前上下文并返回 reset token（调用方 finally 里 restore）。"""
    return _var.set(ctx)


def current() -> ParentContext | None:
    """取当前上下文；未设置返回 None（如存量直连测试）。"""
    return _var.get()


def restore(token) -> None:
    """恢复 push 之前的外层上下文（嵌套安全）。"""
    _var.reset(token)


def clear() -> None:
    """置空当前上下文（兼容无嵌套场景的简单清理）。"""
    _var.set(None)
