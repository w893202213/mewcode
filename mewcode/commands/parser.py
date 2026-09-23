"""斜杠解析器：把一行输入判定为「命令 / 空白 / 普通消息」（ch12 F2）。

命令名大小写不敏感（统一转小写）；参数保留原文——去掉分隔命令名
与参数的第一个空白，其余（包括多余空格）原样保留。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 斜杠 + 命令名（可为空，如输入 "/" 或 "/ x"）+ 可选参数。
# 分隔空白只消费一个字符：其余空白原样保留在参数里（如 "/help  abc" → args=" abc"）。
_SLASH_RE = re.compile(r"^/(\S*)(?:[ \t](.*))?$")


@dataclass(frozen=True)
class ParsedCommand:
    """解析结果：一条已识别的斜杠命令。"""

    name: str  # 小写命令名（不含 /），可能为空串（由分发层报未知命令）
    args: str  # 命令名之后的原文（去掉首个分隔空白），可为空串


def parse_command(text: str) -> ParsedCommand | None:
    """解析一行输入。

    空 / 纯空白 → None（调用方静默继续，不发 Agent）；
    不以 / 开头 → None（普通消息，调用方送 Agent）；
    斜杠命令 → ParsedCommand（name 小写、args 原文）。
    """
    if not text.strip():
        return None
    m = _SLASH_RE.match(text)
    if m is None:
        return None
    return ParsedCommand(name=m.group(1).lower(), args=m.group(2) or "")
