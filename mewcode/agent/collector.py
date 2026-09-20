"""流式双路收集器：增量实时转发 + 完整响应累积。

Agent 收到流事件后一边把增量 yield 给事件流（界面实时渲染），
一边调 add_* 累积——两者同源，保证「界面看到的 = 落盘的」（F4）。
"""

from __future__ import annotations


class StreamCollector:
    """收集一轮 LLM 流中的文本/思考增量，供落盘使用。"""

    def __init__(self) -> None:
        self._text: list[str] = []
        self._thinking: list[str] = []
        self._thinking_raw: list[dict] = []

    def add_text(self, text: str) -> None:
        """追加一段正式回复文本。"""
        self._text.append(text)

    def add_thinking(self, text: str) -> None:
        """追加一段思考文本（仅展示用）。"""
        self._thinking.append(text)

    def set_thinking_raw(self, blocks: list[dict]) -> None:
        """保存原始思考块（Anthropic 多轮要求原样回传）。"""
        self._thinking_raw = list(blocks)

    @property
    def full_text(self) -> str:
        """拼接完成的完整回复文本（落盘用）。"""
        return "".join(self._text)

    @property
    def thinking_text(self) -> str:
        """拼接完成的思考文本（仅展示用）。"""
        return "".join(self._thinking)

    @property
    def thinking_raw(self) -> list[dict]:
        """原始思考块。"""
        return self._thinking_raw
