"""上下文压缩器：两层压缩编排 + 熔断计数（ch09 F3–F6）。

轻量预防（prevent）：回灌时对新消息就地处理——工具结果超阈值存盘，
会话里只留预览与路径，零 LLM 成本、幂等（N4）。

重量兜底（summarize）：整体逼近窗口上限时调 LLM 生成 6 段式结构化
摘要，把摘要区压成「用户原文 + 摘要消息 + 边界消息」，近期原文保留。
连续失败 3 次熔断，自动压缩不再发起；成功一次即清零（F6）。
"""

from __future__ import annotations

from dataclasses import dataclass

from mewcode.agent.events import CompressEvent
from mewcode.context.estimator import TokenEstimator, message_chars
from mewcode.context.prompts import (
    BOUNDARY_NOTICE,
    SummarizeError,
    build_summarize_request,
    parse_summary,
)
from mewcode.context.store import ResultStore, StoreError
from mewcode.provider.base import Provider, SystemPrompt, TextDelta
from mewcode.session import ChatSession, Message

# 摘要连续失败达到该次数即熔断（ch09 F6）
BREAKER_LIMIT = 3

# 摘要输出的预估 token（手动模式保留区预算用，余量兜底）
_SUMMARY_EST_TOKENS = 2000


@dataclass
class SummarizeResult:
    """一次重量兜底压缩的统计。"""

    compacted_messages: int  # 被摘要掉的消息条数
    kept_user_messages: int  # 摘要区原文保留的用户消息条数
    est_saved_tokens: int  # 估算节省 token


class Compressor:
    """两层压缩编排。全部同步：Agent 内经 asyncio.to_thread 调用，/compact 直接调。"""

    def __init__(
        self,
        provider: Provider,
        estimator,
        store: ResultStore,
        context_window: int,
        result_threshold: int = 12000,  # 单条工具结果阈值（字符，F3）
        preview_chars: int = 1000,  # 预览长度（字符）
        auto_margin: int = 13000,  # 自动：触发线 = 窗口 − 13000
        manual_margin: int = 3000,  # 手动：目标水位 = 窗口 − 3000
        keep_tokens: int = 10000,  # 自动模式保留区 ≈10000 token
        min_keep_messages: int = 5,  # 保留区至少 5 条
    ) -> None:
        self._provider = provider
        self._estimator = estimator
        self._store = store
        self._context_window = context_window
        self._result_threshold = result_threshold
        self._preview_chars = preview_chars
        self._auto_margin = auto_margin
        self._manual_margin = manual_margin
        self._keep_tokens = keep_tokens
        self._min_keep_messages = min_keep_messages
        self._failures = 0  # 摘要连续失败计数
        self._seq = 0  # 存盘文件序号（自增）

    # ---- 轻量预防（F3）----

    def prevent(self, msg: Message) -> CompressEvent | None:
        """新消息的工具结果超阈值存盘替换；返回事件（无变化或降级返回 None）。

        按内容从大到小依次存盘，直到「原文合计」低于阈值——单条超阈值
        天然被覆盖。用原文长度跟踪合计（替换后的预览不参与），每条至多
        存盘一次，天然幂等且不会因预览长度死循环（N4）。
        """
        if not msg.tool_results:
            return None
        total = sum(len(r.content) for r in msg.tool_results)
        if total < self._result_threshold:
            return None
        paths: list = []
        for r in sorted(
            msg.tool_results, key=lambda x: len(x.content), reverse=True
        ):
            if total < self._result_threshold:
                break
            self._seq += 1
            try:
                path = self._store.save(r.content, self._seq)
            except StoreError:
                break  # 降级：本条保持原文，不阻断请求（N6）
            paths.append(path)
            total -= len(r.content)  # 从原文合计中扣除
            r.content = self._store.preview(
                r.content, path, self._preview_chars
            )
        if not paths:
            return None
        return CompressEvent(
            kind="prevent",
            message=(
                f"工具结果较大，已存盘至 {paths[-1]}"
                f"（对话中仅保留预览，需要完整内容时模型会读取该文件）"
            ),
            path=str(paths[-1]),
        )

    # ---- 重量兜底（F4/F5）----

    def _is_user_text(self, m: Message) -> bool:
        """纯用户文本消息（不含工具结果）——压缩时原文保留（N1）。"""
        return m.role == "user" and not m.tool_results

    def _keep_budget(self, messages: list[Message], manual: bool) -> int:
        """保留区 token 预算。

        自动：固定 ≈10000 token（保守，给后续工作留空间）。
        手动：目标水位 ≤ 窗口 − 3000——预算 = 窗口 − 余量 − 全部用户消息
        （原文保留、压不动）− 摘要预估 − 边界，即用户主动压时可保留更多
        近期原文（F5）。
        """
        if not manual:
            return self._keep_tokens
        user_tokens = sum(
            message_chars(m) for m in messages if self._is_user_text(m)
        ) // TokenEstimator.CHARS_PER_TOKEN
        boundary_tokens = len(BOUNDARY_NOTICE) // TokenEstimator.CHARS_PER_TOKEN
        budget = (
            self._context_window
            - self._manual_margin
            - user_tokens
            - _SUMMARY_EST_TOKENS
            - boundary_tokens
        )
        return max(budget, 0)

    def _select_keep_start(
        self, messages: list[Message], budget_tokens: int
    ) -> int:
        """从尾部按估算 token 往回数，选出保留区起点（至少 min_keep 条）。"""
        keep_start = len(messages)
        est_tokens = 0
        for i in range(len(messages) - 1, -1, -1):
            tok = message_chars(messages[i]) // TokenEstimator.CHARS_PER_TOKEN
            kept_count = len(messages) - i  # 含本条在内的保留条数
            if est_tokens + tok > budget_tokens and kept_count > self._min_keep_messages:
                break  # 超出预算且已保足下限：本条不再保留
            est_tokens += tok
            keep_start = i
        return keep_start

    def summarize(self, session: ChatSession, manual: bool = False) -> SummarizeResult:
        """重量兜底压缩（同步）：选保留区 → LLM 摘要 → 重构会话。失败抛 SummarizeError 并计数。"""
        messages = session.messages
        keep_start = self._select_keep_start(
            messages, self._keep_budget(messages, manual)
        )
        head = messages[:keep_start]  # 摘要区
        kept_users = [m for m in head if self._is_user_text(m)]
        chunks = [m for m in head if not self._is_user_text(m)]
        if not chunks:
            # 摘要区没有可摘要内容（消息太少，全进保留区）：无需压缩
            return SummarizeResult(0, 0, 0)
        before_chars = sum(message_chars(m) for m in messages)
        try:
            system, request = build_summarize_request(chunks)
            text_parts: list[str] = []
            # 摘要请求不携带任何工具（F4）；同步消费全部文本增量
            for event in self._provider.stream(request, [], system):
                if isinstance(event, TextDelta):
                    text_parts.append(event.text)
            summary_text = parse_summary("".join(text_parts))
        except Exception as e:
            self._failures += 1
            raise SummarizeError(f"摘要生成失败: {e}") from e
        # 成功一次即清零熔断计数（F6）
        self._failures = 0
        new_head = list(kept_users) + [
            Message(role="user", text=summary_text),  # 摘要消息
            Message(role="user", text=BOUNDARY_NOTICE),  # 边界消息
        ]
        session.replace_head(keep_start, new_head)
        after_chars = sum(message_chars(m) for m in session.messages)
        return SummarizeResult(
            compacted_messages=len(chunks),
            kept_user_messages=len(kept_users),
            # 系统提示压缩前后相同、互相抵消，按消息字符差分估算节省
            est_saved_tokens=max(0, before_chars - after_chars) // TokenEstimator.CHARS_PER_TOKEN,
        )

    # ---- 触发判定与熔断（F4/F6）----

    def needs_auto(self, messages: list[Message], system: SystemPrompt) -> bool:
        """自动触发判定：估算总量 ≥ 窗口 − 13000 且未熔断。"""
        if self.breaker_open:
            return False
        return (
            self._estimator.estimate(messages, system)
            >= self._context_window - self._auto_margin
        )

    @property
    def breaker_open(self) -> bool:
        """摘要连续失败 ≥ 3 次：自动压缩熔断。"""
        return self._failures >= BREAKER_LIMIT
