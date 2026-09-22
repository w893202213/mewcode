"""会话层：对话历史的内存维护，不感知任何协议。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolUse:
    """归一化的工具调用（assistant 消息携带，多轮时由 provider 按协议重建）。"""

    id: str  # 工具调用唯一 ID（回灌结果时原样引用）
    name: str  # 工具名
    args: dict  # 完整参数


@dataclass
class ToolResultRecord:
    """归一化的工具结果（user 消息携带）。"""

    tool_use_id: str
    content: str  # 成功内容或中文错误信息
    is_error: bool  # 失败结果原样标记回传


@dataclass
class Message:
    """一轮对话消息。"""

    role: str  # "user" | "assistant"
    text: str  # 正式回复文本（user 消息即用户输入）
    thinking: str = ""  # 思考内容拼接文本（仅展示用）
    thinking_raw: list[dict] = field(default_factory=list)  # 原始思考块，多轮时原样回传
    tool_uses: list[ToolUse] = field(default_factory=list)  # assistant 消息的工具调用
    tool_results: list[ToolResultRecord] = field(default_factory=list)  # user 消息的工具结果
    summary: bool = False  # 压缩产物标记（摘要消息/边界消息）：二次压缩时不再当作用户原文（ch09）


class ChatSession:
    """对话会话：仅存于进程内存，退出即丢弃。"""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self.messages.append(Message(role="user", text=text))

    def add_assistant(
        self,
        text: str,
        thinking: str = "",
        thinking_raw: list[dict] | None = None,
        tool_uses: list[ToolUse] | None = None,
    ) -> None:
        """追加一条助手消息（含可选的思考内容、原始思考块与工具调用）。"""
        self.messages.append(
            Message(
                role="assistant",
                text=text,
                thinking=thinking,
                thinking_raw=list(thinking_raw or []),
                tool_uses=list(tool_uses or []),
            )
        )

    def add_tool_result(self, record: ToolResultRecord) -> None:
        """追加一条承载工具结果的 user 消息（协议侧表现为 tool_result / tool 消息）。"""
        self.messages.append(
            Message(role="user", text="", tool_results=[record])
        )

    def clear(self) -> None:
        """清空全部历史。"""
        self.messages.clear()

    def replace_head(self, end: int, new_messages: list[Message]) -> None:
        """丢弃前 end 条消息，头部换成 new_messages（ch09 压缩重构用）。

        end 越界抛 ValueError——调用方必须先选好保留区起点。
        """
        if end < 0 or end > len(self.messages):
            raise ValueError(f"replace_head 越界: end={end}，消息总数 {len(self.messages)}")
        self.messages[:end] = new_messages
