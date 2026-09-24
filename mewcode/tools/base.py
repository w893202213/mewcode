"""工具系统基础：统一 Tool 接口、结构化结果与执行包装。

六个核心工具全部实现 Tool 接口；超时/异常/截断等横切关注点
集中在 run_tool 统一处理，具体工具只写业务逻辑。
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar

from mewcode.agents.context import clear, current, push

# 回灌给模型与展示在 TUI 的工具结果长度上限（spec N2）
RESULT_MAX_CHARS = 30000

# 统一执行超时上限（秒，spec N1）
DEFAULT_TIMEOUT = 30.0


class ToolError(Exception):
    """工具层错误，消息一律中文、可读。"""


@dataclass
class ToolResult:
    """工具执行的结构化结果：成功内容或中文错误信息。"""

    ok: bool
    content: str

    @classmethod
    def success(cls, content: str) -> "ToolResult":
        return cls(ok=True, content=content)

    @classmethod
    def failure(cls, content: str) -> "ToolResult":
        return cls(ok=False, content=content)


class Tool(ABC):
    """统一工具接口：元信息（模型可见）+ 执行方法。"""

    name: ClassVar[str]  # 工具名（模型可见）
    description: ClassVar[str]  # 工具描述（模型可见）
    input_schema: ClassVar[dict]  # 参数 JSON Schema（模型可见）
    timeout: ClassVar[float] = DEFAULT_TIMEOUT  # 执行超时上限（秒）
    requires_confirmation: ClassVar[bool] = False  # 执行前是否需人工确认（待后续安全章节）
    read_only: ClassVar[bool] = True  # 只读标记：plan 模式过滤 + 分批并发判定共用

    @abstractmethod
    def execute(self, args: dict) -> ToolResult:
        """执行业务逻辑；抛异常由 run_tool 统一转成错误结果。"""
        ...


def _truncate(content: str) -> str:
    """超长内容截断并附「已截断」说明（spec N2）。"""
    if len(content) <= RESULT_MAX_CHARS:
        return content
    return content[:RESULT_MAX_CHARS] + f"\n…（已截断，共 {len(content)} 字符）"


def run_tool(tool: Tool, args: dict) -> ToolResult:
    """统一执行包装：守护线程 + join 超时；异常转错误结果；成功内容截断。

    线程超时后无法强杀，守护线程会在后台自然结束；命令类工具
    自身另有 subprocess 超时兜底，保证子进程不会悬挂。
    ch16：捕获调用线程的 ParentContext（读缓存/父 Agent）并在执行
    线程内重设——threading.local 不跨线程。
    """
    box: dict = {}
    ctx = current()  # 调用线程上下文（无则 None，执行线程同样无）

    def _execute() -> None:
        if ctx is not None:
            push(ctx)
        try:
            box["result"] = tool.execute(args)
        except Exception as e:  # 工具实现抛出的任何异常都转为错误结果（spec F7）
            box["result"] = ToolResult.failure(f"工具执行异常: {e}")
        finally:
            if ctx is not None:
                clear()

    thread = threading.Thread(target=_execute, daemon=True)
    thread.start()
    thread.join(tool.timeout)
    if thread.is_alive():
        return ToolResult.failure(f"工具执行超时（{tool.timeout:g} 秒）")
    result: ToolResult = box["result"]
    return ToolResult(ok=result.ok, content=_truncate(result.content))
