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
class UsageEvent:
    """一次 LLM 调用的 token 用量（TUI 累计展示）。"""

    input_tokens: int
    output_tokens: int


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
    | UsageEvent
    | ProgressEvent
    | RoundEnd
)
