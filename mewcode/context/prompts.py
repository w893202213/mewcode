"""摘要 Prompt 构建与响应解析（ch09 F4）。

重量兜底的摘要请求：system.stable = 摘要指令（禁止工具、先草稿后正式、
6 固定部分），messages = 单条 user 消息承载待摘要内容；响应只取
<正式摘要> 段，草稿段解析后即弃（N5）。
"""

from __future__ import annotations

import json

from mewcode.provider.base import SystemPrompt
from mewcode.session import Message


class SummarizeError(Exception):
    """摘要生成/解析失败，消息中文、可读（压缩器据此熔断计数）。"""


# 摘要专用系统指令（stable 走缓存前缀，无副作用）
SUMMARY_SYSTEM = """\
你是会话历史压缩器。本请求的目的是把较早的对话历史压缩成结构化摘要，替代原文进入后续对话。

要求：
1. 禁止调用任何工具。本请求不携带工具，你只能输出文本。
2. 先在 <分析草稿> 段里梳理历史要点（该段不会被使用），再输出 <正式摘要> 段。
3. 正式摘要必须严格按以下 6 个固定部分组织，每部分以 ## 开头：
   ## 总体目标与状态
   ## 已完成工作
   ## 关键文件与代码
   ## 重要事实与决策
   ## 未完成事项
   ## 注意事项
4. 正式摘要之外的一切输出都会被忽略。涉及代码时给出关键路径与要点，不要整段抄录。"""

# 压缩后插入的边界消息：提醒模型摘要不是原文，细节以文件为准
BOUNDARY_NOTICE = (
    "以上是本次压缩生成的会话摘要，不是原始对话。"
    "涉及文件内容、代码细节时请重新读取文件确认，"
    "不要直接依据摘要中的代码片段修改文件。"
)

# 待摘要内容单条消息的字符上限：防止摘要请求本身过大
_INPUT_CHUNK_CHARS = 4000


def _format_chunk(m: Message) -> str:
    """把一条待摘要消息转成带前缀的文本块，超长截断。"""
    if m.role == "user" and m.tool_results:
        body = "\n---\n".join(
            f"{'[失败]' if r.is_error else ''}{r.content}" for r in m.tool_results
        )
    elif m.role == "assistant" and m.tool_uses:
        calls = "; ".join(
            f"{u.name}({json.dumps(u.args, ensure_ascii=False)})" for u in m.tool_uses
        )
        body = f"{m.text}\n[工具调用] {calls}"
    else:
        body = m.text
    if len(body) > _INPUT_CHUNK_CHARS:
        body = body[:_INPUT_CHUNK_CHARS] + "\n…（已截断）"
    label = "[工具结果]" if (m.role == "user" and m.tool_results) else (
        "[用户消息]" if m.role == "user" else "[助手回复]"
    )
    return f"{label}\n{body}"


def build_summarize_request(
    messages_to_summarize: list[Message],
) -> tuple[SystemPrompt, list[Message]]:
    """组装摘要请求：system 只带摘要指令，messages 为单条 user 消息承载待摘要内容。"""
    content = "\n\n".join(_format_chunk(m) for m in messages_to_summarize)
    request = Message(
        role="user",
        text=f"请把以下对话历史压缩成结构化摘要（按系统指令的 6 个部分组织）：\n\n{content}",
    )
    return SystemPrompt(stable=SUMMARY_SYSTEM, dynamic=""), [request]


def parse_summary(text: str) -> str:
    """提取 <正式摘要> 段；缺失或为空抛 SummarizeError（保守口径，防半截内容进会话）。"""
    start = text.find("<正式摘要>")
    end = text.find("</正式摘要>")
    if start == -1 or end == -1 or end <= start:
        raise SummarizeError("摘要响应格式不符：缺少 <正式摘要> 段")
    summary = text[start + len("<正式摘要>") : end].strip()
    if not summary:
        raise SummarizeError("摘要响应格式不符：<正式摘要> 段为空")
    return summary
