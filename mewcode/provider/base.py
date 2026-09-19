"""Provider 抽象层：统一流接口与归一化流事件。

TUI 只依赖本模块定义的接口，完全不感知具体协议。
新增后端 = 实现 Provider 子类 + 在注册表（__init__.py）加一行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

from mewcode.session import Message


@dataclass
class ThinkingDelta:
    """思考内容增量 → TUI 灰色缩进实时展示。"""

    text: str


@dataclass
class TextDelta:
    """正式回复增量 → TUI 追加 Markdown buffer 刷新。"""

    text: str


@dataclass
class Done:
    """流正常结束。"""


# 归一化流事件联合类型
StreamEvent = ThinkingDelta | TextDelta | Done


class ProviderError(Exception):
    """Provider 层错误，消息一律中文、可读，由 TUI 捕获显示。"""


class Provider(ABC):
    """LLM 后端统一接口。

    协议转换（Message → 请求体）在子类内部完成，调用方只看到 StreamEvent。
    本步为同步迭代器：流式期间 TUI 本就阻塞（不做打断），异步徒增复杂度。
    """

    @abstractmethod
    def stream(self, messages: list[Message]) -> Iterator[StreamEvent]:
        """把对话历史发给 LLM，逐个产出归一化流事件；失败抛 ProviderError。"""
        ...
