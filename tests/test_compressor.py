"""上下文压缩器测试（ch09 F3–F6）。"""

import pytest

from mewcode.agent.events import CompressEvent
from mewcode.context.compressor import Compressor, SummarizeResult
from mewcode.context.estimator import TokenEstimator
from mewcode.context.prompts import SummarizeError
from mewcode.context.store import ResultStore, StoreError
from mewcode.provider.base import ProviderError, SystemPrompt, TextDelta
from mewcode.session import ChatSession, Message, ToolResultRecord

SUMMARY_REPLY = "<分析草稿>\n草稿内容\n</分析草稿>\n<正式摘要>\n## 总体目标与状态\n测试摘要\n</正式摘要>"

SYSTEM = SystemPrompt(stable="系统", dynamic="")


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []  # (messages, tools) 快照，供断言

    def stream(self, messages, tools, system):
        self.calls.append((list(messages), list(tools)))
        return iter(self._handler(len(self.calls), messages, tools))


class FailingStore:
    """存盘必然失败的假 store（鸭子类型替代 ResultStore）。"""

    def save(self, content, seq):
        raise StoreError("磁盘不可写")

    def preview(self, content, path, head_chars):
        return "预览"


def _compressor(tmp_path, provider=None, **kw):
    """构造压缩器：真实估算器 + 临时目录存盘 + 可注入 provider。"""
    provider = provider or FakeProvider(lambda n, m, t: [TextDelta(SUMMARY_REPLY)])
    store = ResultStore(tmp_path / "ctx")
    kw.setdefault("context_window", 200000)
    return Compressor(provider, TokenEstimator(), store, **kw)


def _session():
    """5 条长消息的会话（短消息字符÷4 为 0，会干扰保留区选择）。"""
    s = ChatSession()
    s.add_user("问题一" * 50)
    s.add_assistant("回答一" * 50)
    s.add_tool_result(ToolResultRecord("1", "结果一" * 50, False))
    s.add_user("问题二" * 50)
    s.add_assistant("回答二" * 50)
    return s


def _summarize_session(tmp_path, provider=None, **kw):
    """跑一次 summarize（保证摘要区非空），返回（压缩器, 会话, 结果）。"""
    kw.setdefault("keep_tokens", 10)  # 每条约 37 token：保留尾部 3 条
    kw.setdefault("min_keep_messages", 3)
    c = _compressor(tmp_path, provider=provider, **kw)
    s = _session()
    result = c.summarize(s)
    return c, s, result


# ---- 轻量预防 ----

def test_prevent_single_large_result_stores_file(tmp_path):
    c = _compressor(tmp_path, result_threshold=50)
    msg = Message(
        role="user",
        text="",
        tool_results=[ToolResultRecord("1", "x" * 5000, False)],
    )
    event = c.prevent(msg)
    assert isinstance(event, CompressEvent) and event.kind == "prevent"
    stored = msg.tool_results[0].content
    assert "x" * 5000 not in stored  # 原文不在会话里
    assert "文件路径" in stored and "请读取该文件" in stored
    assert len(stored) < 2000  # 只留预览
    path = tmp_path / "ctx" / "tool_result_1.txt"
    assert path.exists() and path.read_text(encoding="utf-8") == "x" * 5000


def test_prevent_below_threshold_noop(tmp_path):
    c = _compressor(tmp_path, result_threshold=50)
    msg = Message(
        role="user",
        text="",
        tool_results=[ToolResultRecord("1", "短结果", False)],
    )
    assert c.prevent(msg) is None
    assert msg.tool_results[0].content == "短结果"


def test_prevent_total_over_threshold_largest_first(tmp_path):
    c = _compressor(tmp_path, result_threshold=60)
    msg = Message(
        role="user",
        text="",
        tool_results=[
            ToolResultRecord("1", "a" * 20, False),
            ToolResultRecord("2", "b" * 3000, False),  # 最大
            ToolResultRecord("3", "c" * 30, False),
        ],
    )
    event = c.prevent(msg)
    assert event is not None
    # 最大的先被存盘：会话里 b 原文消失，其余保留（合计 20+30 < 60 停止）
    assert "b" * 3000 not in msg.tool_results[1].content
    assert "文件路径" in msg.tool_results[1].content
    assert msg.tool_results[0].content == "a" * 20
    assert msg.tool_results[2].content == "c" * 30


def test_prevent_store_failure_degrades(tmp_path):
    c = _compressor(tmp_path, result_threshold=10)
    c._store = FailingStore()
    msg = Message(
        role="user",
        text="",
        tool_results=[ToolResultRecord("1", "x" * 100, False)],
    )
    assert c.prevent(msg) is None  # 降级：不阻断
    assert msg.tool_results[0].content == "x" * 100  # 保持原文


# ---- 重量兜底 ----

def test_summarize_restructures_session(tmp_path):
    c, s, result = _summarize_session(tmp_path)
    texts = [m.text for m in s.messages]
    assert texts[0] == "问题一" * 50  # 摘要区用户消息原文保留
    assert any("测试摘要" in t for t in texts)  # 摘要消息
    assert any("不是原始对话" in t for t in texts)  # 边界消息
    assert texts[-2:] == ["问题二" * 50, "回答二" * 50]  # 保留区原文不动
    assert result.compacted_messages == 1
    assert result.kept_user_messages == 1
    assert result.est_saved_tokens > 0


def test_summarize_request_has_no_tools(tmp_path):
    """摘要 LLM 请求不带任何工具（AC6）。"""
    c, s, _ = _summarize_session(tmp_path)
    _, tools = c._provider.calls[0]
    assert tools == []


def test_summarize_empty_chunks_returns_zero(tmp_path):
    """消息太少全进保留区：不调 LLM，直接返回零统计。"""
    c = _compressor(tmp_path)
    s = ChatSession()
    s.add_user("你好")
    s.add_assistant("你好！")
    result = c.summarize(s)
    assert result == SummarizeResult(0, 0, 0)
    assert c._provider.calls == []  # 未调用 LLM


def test_summarize_failure_counts_toward_breaker(tmp_path):
    def fail(n, m, t):
        raise ProviderError("模拟失败")

    c = _compressor(tmp_path, provider=FakeProvider(fail), keep_tokens=10, min_keep_messages=3)
    s = _session()
    for _ in range(3):
        with pytest.raises(SummarizeError):
            c.summarize(s)
    assert c.breaker_open
    assert c.needs_auto(s.messages, SYSTEM) is False


def test_summarize_success_resets_breaker(tmp_path):
    def fail_then_ok(n, m, t):
        if n <= 3:
            raise ProviderError("模拟失败")
        return [TextDelta(SUMMARY_REPLY)]

    c = _compressor(tmp_path, provider=FakeProvider(fail_then_ok), keep_tokens=10, min_keep_messages=3)
    s = _session()
    for _ in range(3):
        with pytest.raises(SummarizeError):
            c.summarize(s)
    assert c.breaker_open
    c.summarize(s)  # 第 4 次成功
    assert not c.breaker_open


def test_summarize_missing_formal_section_fails(tmp_path):
    def bad_reply(n, m, t):
        return [TextDelta("没有正式摘要段")]

    c = _compressor(tmp_path, provider=FakeProvider(bad_reply), keep_tokens=10, min_keep_messages=3)
    s = _session()
    with pytest.raises(SummarizeError):
        c.summarize(s)


def test_manual_mode_budget_stays_below_margin(tmp_path):
    """手动模式：压缩后估算总量 ≤ 窗口 − 3000（AC7）。"""
    window = 10000
    c = _compressor(tmp_path, context_window=window)
    s = _session()
    # 大量工具结果/回复撑大历史（用户消息少，压得动）
    for i in range(40):
        s.add_tool_result(ToolResultRecord(str(i), "结果" * 1000, False))
        s.add_assistant("回答" * 1000)
    result = c.summarize(s, manual=True)
    assert result.compacted_messages > 0
    est = c._estimator.estimate(s.messages, SYSTEM)
    assert est <= window - 3000


def test_needs_auto_below_threshold_false(tmp_path):
    c = _compressor(tmp_path, context_window=5000, auto_margin=3000)
    s = _session()
    assert c.needs_auto(s.messages, SYSTEM) is False


def test_needs_auto_above_threshold_true(tmp_path):
    c = _compressor(tmp_path, context_window=5000, auto_margin=4000)
    s = _session()
    s.add_assistant("超长" * 2000)  # 撑大估算至 ≥ 窗口 − 4000
    assert c.needs_auto(s.messages, SYSTEM) is True
