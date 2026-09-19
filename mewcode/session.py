"""会话层：对话历史的内存维护，不感知任何协议。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """一轮对话消息。"""

    role: str  # "user" | "assistant"
    text: str  # 正式回复文本（user 消息即用户输入）
    thinking: str = ""  # 思考内容拼接文本（仅展示用）
    thinking_raw: list[dict] = field(default_factory=list)  # 原始思考块，多轮时原样回传


class ChatSession:
    """对话会话：仅存于进程内存，退出即丢弃。"""

    def __init__(self) -> None:
        self.messages: list[Message] = []

    def add_user(self, text: str) -> None:
        """追加一条用户消息。"""
        self.messages.append(Message(role="user", text=text))

    def add_assistant(self, text: str, thinking: str = "", thinking_raw: list[dict] | None = None) -> None:
        """追加一条助手消息（含可选的思考内容与原始思考块）。"""
        self.messages.append(
            Message(
                role="assistant",
                text=text,
                thinking=thinking,
                thinking_raw=list(thinking_raw or []),
            )
        )

    def clear(self) -> None:
        """清空全部历史。"""
        self.messages.clear()
