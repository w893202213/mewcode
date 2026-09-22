"""Token 近似估算：锚定 API usage、增量按字符差分估算（ch09 F2）。

不做精确 tokenizer：最近一次成功请求的 input_tokens 是真实值，
之后新增/删除的消息只对字符增量按「4 字符 ≈ 1 token」折算。
用字符总量差分而非消息索引定位增量——压缩替换头部后消息索引失效，
差分对「追加 / 替换头部」两类变更都成立。
"""

from __future__ import annotations

import json

from mewcode.provider.base import SystemPrompt
from mewcode.session import Message


def message_chars(m: Message) -> int:
    """一条消息的字符口径：正文 + 思考 + 工具结果 + 工具参数 JSON。

    与 provider 发出的内容对齐（thinking_raw 随 assistant 消息原样回传，
    同样占用输入）。
    """
    chars = len(m.text) + len(m.thinking)
    chars += sum(len(r.content) for r in m.tool_results)
    chars += sum(len(json.dumps(u.args, ensure_ascii=False)) for u in m.tool_uses)
    return chars


def _total_message_chars(messages: list[Message]) -> int:
    """全部消息的字符总量（增量差分的基准）。"""
    return sum(message_chars(m) for m in messages)


class TokenEstimator:
    """近似估算器：有锚点用「锚点 + 增量」，无锚点全量按字符折算。"""

    CHARS_PER_TOKEN = 4  # 近似比例：4 字符 ≈ 1 token

    def __init__(self) -> None:
        self._anchor_tokens: int | None = None  # 锚点：最近一次 usage.input_tokens
        self._anchor_chars: int | None = None  # 锚点：该次请求的消息字符总量快照

    def anchor(self, input_tokens: int, messages: list[Message]) -> None:
        """记录锚点：最近一次成功请求的输入 token 与当时消息字符总量。

        锚点值已含当时系统提示与工具定义（各家 usage 口径一致），
        之后只需对消息字符增量折算；system.dynamic 的轮次变化忽略（余量兜底）。
        """
        self._anchor_tokens = input_tokens
        self._anchor_chars = _total_message_chars(messages)

    def estimate(self, messages: list[Message], system: SystemPrompt) -> int:
        """估算当前请求的输入 token 总量（消息 + 系统提示，不含工具定义）。"""
        current = _total_message_chars(messages)
        if self._anchor_tokens is None or self._anchor_chars is None:
            # 无锚点：全量按字符折算（消息 + 系统提示）
            return (
                current + len(system.stable) + len(system.dynamic)
            ) // self.CHARS_PER_TOKEN
        # 有锚点：锚点值 + 消息字符增量（压缩使字符下降时增量取 0，估算不低于锚点）
        delta = max(0, current - self._anchor_chars)
        return self._anchor_tokens + delta // self.CHARS_PER_TOKEN
