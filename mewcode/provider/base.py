"""Provider 抽象层：统一流接口与归一化流事件。

TUI 只依赖本模块定义的接口，完全不感知具体协议。
新增后端 = 实现 Provider 子类 + 在注册表（__init__.py）加一行。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from mewcode.session import Message
from mewcode.tools.base import Tool


@dataclass
class ThinkingDelta:
    """思考内容增量 → TUI 灰色缩进实时展示。"""

    text: str


@dataclass
class TextDelta:
    """正式回复增量 → TUI 追加 Markdown buffer 刷新。"""

    text: str


@dataclass
class ToolCall:
    """模型发起的工具调用（参数已由流式碎片拼接完整）。

    parse_error 非空表示参数 JSON 无法解析，调用方不应执行该工具，
    而是把错误信息回给模型（spec F7）。
    """

    id: str  # 调用唯一 ID（回灌 tool_result 时原样引用）
    name: str  # 工具名
    args: dict  # 拼接完成的参数
    parse_error: str | None = None  # 参数解析失败的中文说明


@dataclass
class UsageInfo:
    """一次 LLM 调用的 token 用量（各家协议解析后归一化）。

    缓存字段归一化：cache_hit_tokens = 从缓存读取的量（Anthropic 的
    cache_read / OpenAI 兼容服务的 prompt_cache_hit），cache_write_tokens
    = 本次写入缓存的量（Anthropic 的 cache_creation）。服务端不返回时
    为 None，展示层据此优雅降级。
    """

    input_tokens: int
    output_tokens: int
    cache_hit_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass
class SystemPrompt:
    """一次请求的系统提示：stable 进入可缓存前缀，dynamic 紧随其后、不缓存。

    两家协议各自映射：
    - Anthropic：system 块列表——stable 块末尾加缓存断点，dynamic 作为
      断点后的补充块（无断点，每轮变化不打断缓存前缀）
    - OpenAI：system 角色消息——stable 排在最前（依赖服务端自动前缀
      缓存），dynamic 拼接其后
    """

    stable: str  # 七个固定模块文本（每轮逐字节一致）
    dynamic: str  # <supplement>补充块 + 自定义指令块（都无则空串）


@dataclass
class Done:
    """流正常结束，附带需要回传的原始思考块（Anthropic 多轮要求原样回传）
    与本轮 token 用量（协议流未携带时为 None）。"""

    thinking_raw: list[dict] = field(default_factory=list)
    usage: UsageInfo | None = None


# 归一化流事件联合类型
StreamEvent = ThinkingDelta | TextDelta | ToolCall | Done


class ProviderError(Exception):
    """Provider 层错误，消息一律中文、可读，由 TUI 捕获显示。"""


class Provider(ABC):
    """LLM 后端统一接口。

    协议转换（Message → 请求体、Tool 元信息 → 各家 tools 字段）在子类内部完成，
    调用方只看到 StreamEvent。本步为同步迭代器：流式期间 TUI 本就阻塞
    （不做打断），异步徒增复杂度。
    """

    @abstractmethod
    def stream(
        self, messages: list[Message], tools: list[Tool], system: SystemPrompt
    ) -> Iterator[StreamEvent]:
        """把对话历史、工具列表与系统提示发给 LLM，逐个产出归一化流事件；失败抛 ProviderError。

        system.stable 必须落在可缓存前缀上（协议允许时加缓存断点），
        system.dynamic 紧随其后且绝不携带缓存断点。
        """
        ...
