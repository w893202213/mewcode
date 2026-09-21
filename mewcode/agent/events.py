"""Agent 事件流类型：Agent 与界面之间的唯一契约。

Agent 只产出事件、完全不感知界面；TUI 只依赖本模块的 AgentEvent
联合类型渲染。文本/思考增量复用 provider 的事件类型（界面已认识），
工具执行、Token 用量、进度与轮次结束为 Agent Loop 新增。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from mewcode.provider.base import TextDelta, ThinkingDelta, ToolCall
from mewcode.tools.base import ToolResult


@dataclass
class ToolCallStarted:
    """一个工具调用即将执行（TUI 展示工具名+参数）。"""

    call: ToolCall


@dataclass
class ToolResultEvent:
    """一个工具调用的执行结果（TUI 展示结果面板）。"""

    call: ToolCall
    result: ToolResult


@dataclass
class PermissionAskEvent:
    """一个工具调用触发询问（ch07 人在回路）：Agent 挂起等待应答。

    TUI 展示询问面板并在用户作答后调用 Agent.answer_permission()；
    reason 说明触发询问的原因（ask 规则或默认档询问）。
    """

    call: ToolCall
    reason: str


@dataclass
class UsageEvent:
    """本轮循环截至当前的累计 token 用量（每次 LLM 调用后发射，TUI 展示）。

    缓存字段为累计值：服务端不返回时为 None，TUI 据此优雅降级不展示。
    """

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int | None = None  # 本轮累计缓存命中（从缓存读取的量）
    cache_write_tokens: int | None = None  # 本轮累计缓存写入（写入缓存的量）


@dataclass
class ProgressEvent:
    """第 N 轮循环开始（从 1 起，TUI 显示进度）。"""

    iteration: int


class RoundEndReason(Enum):
    """轮次结束原因（spec F2 五种停止条件）。"""

    OK = "ok"  # 模型不再调工具，正常结束
    LIMIT = "limit"  # 达迭代上限
    CANCELLED = "cancelled"  # 用户取消
    UNKNOWN_TOOL = "unknown_tool"  # 连续调用未知工具
    ERROR = "error"  # 流出错（本轮已回滚）


@dataclass
class RoundEnd:
    """事件流收尾事件：附带停止原因与中文说明。收到后不再有其他事件（N2）。"""

    reason: RoundEndReason
    message: str = ""


# 事件流联合类型（文本/思考增量复用 provider 定义，保持界面兼容）
AgentEvent = (
    TextDelta
    | ThinkingDelta
    | ToolCallStarted
    | ToolResultEvent
    | PermissionAskEvent
    | UsageEvent
    | ProgressEvent
    | RoundEnd
)
