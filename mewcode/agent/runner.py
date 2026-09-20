"""ToolRunner：工具调用的执行与结果回灌。

职责：按名查工具 → 确认拦截 → 执行（超时/异常由 run_tool 统一处理）
→ 结果写回会话历史。不感知 LLM 协议，不感知 TUI；确认交互通过
注入的 confirm 回调完成（测试时可注入 lambda）。
"""

from __future__ import annotations

from collections.abc import Callable

from mewcode.provider.base import ToolCall
from mewcode.session import ChatSession, ToolResultRecord
from mewcode.tools.base import ToolError, ToolResult, run_tool
from mewcode.tools.registry import ToolRegistry


class ToolRunner:
    """执行模型发起的工具调用，并把结果回灌进会话。"""

    def __init__(
        self,
        registry: ToolRegistry,
        session: ChatSession,
        confirm: Callable[[str], bool] | None = None,
    ) -> None:
        self._registry = registry
        self._session = session
        self._confirm = confirm

    def run(self, call: ToolCall) -> ToolResult:
        """执行一个工具调用并回灌会话，返回结果供 TUI 展示。

        任何失败路径（参数解析失败、未知工具、确认拒绝、执行错误）
        都不抛异常，而是返回 ok=False 的结构化结果。
        """
        if call.parse_error:
            result = ToolResult.failure(call.parse_error)
            self._record(call, result)
            return result

        try:
            tool = self._registry.get(call.name)
        except ToolError as e:
            result = ToolResult.failure(str(e))
            self._record(call, result)
            return result

        if tool.requires_confirmation and self._confirm is not None:
            command = str(call.args.get("command", ""))
            if not self._confirm(command):
                result = ToolResult.failure("用户拒绝了该命令的执行")
                self._record(call, result)
                return result

        result = run_tool(tool, call.args)
        self._record(call, result)
        return result

    def _record(self, call: ToolCall, result: ToolResult) -> None:
        """把工具结果作为一条 user 消息写入会话（下一轮请求回放给模型）。"""
        self._session.add_tool_result(
            ToolResultRecord(
                tool_use_id=call.id,
                content=result.content,
                is_error=not result.ok,
            )
        )
