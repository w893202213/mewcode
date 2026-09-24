"""父上下文传递载体（ch16）：threading.local 让工具与 Hook 动作在
执行线程内拿到父 Agent、任务管理器与读缓存，不改任何工具接口签名。

loop.py 的 to_thread 包装在每个工具执行线程入口 push，finally clear；
AgentTool.execute 与 exec_subagent 经 current() 读取。类型用 Any 避免
与 loop.py / manager.py 的循环依赖（运行时对象引用）。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class ParentContext:
    """一个工具执行线程内的父上下文快照。"""

    agent: Any = None  # 父 Agent 引用（Fork 取消息/可见工具/档位/stable 参数）
    task_manager: Any = None  # TaskManager（agent 工具与 hook subagent 动作用）
    read_cache: Any = None  # FileReadCache 实例（read_file 缓存隔离载体）


_local = threading.local()


def push(ctx: ParentContext) -> None:
    """设置当前线程的上下文（工具执行入口调用）。"""
    _local.ctx = ctx


def current() -> ParentContext | None:
    """取当前线程的上下文；未设置返回 None（如存量直连测试）。"""
    return getattr(_local, "ctx", None)


def clear() -> None:
    """清除当前线程的上下文（finally 调用，防线程池线程串味）。"""
    _local.ctx = None
