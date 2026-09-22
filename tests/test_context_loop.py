"""Agent Loop 上下文压缩集成测试（ch09 F3–F6）。

自动触发场景用「锚点撑大估算」：FakeProvider 返回 input_tokens=190000
的 usage（窗口 200000，阈值 187000），下一轮请求前即触发重量兜底；
消息靠多轮中等结果（不触发轻量预防）累积到总量超过保留区预算，
让摘要区非空。
"""

import asyncio

from mewcode.agent.events import CompressEvent, RoundEnd, RoundEndReason, TextDelta
from mewcode.agent.loop import Agent
from mewcode.provider.base import (
    Done,
    ProviderError,
    TextDelta as PTextDelta,
    ToolCall,
    UsageInfo,
)
from mewcode.session import ChatSession, ToolResultRecord
from mewcode.tools import create_registry
from mewcode.tools.base import Tool, ToolResult

SUMMARY_REPLY = "<分析草稿>\n草稿\n</分析草稿>\n<正式摘要>\n## 总体目标与状态\n集成摘要\n</正式摘要>"

WINDOW = 200000  # 窗口上限；自动阈值 = 200000 − 13000 = 187000


class BigTool(Tool):
    """返回 30000 字符大结果的只读工具（超过 12000 阈值，触发轻量预防）。"""

    name = "big"
    description = "返回大结果"
    input_schema = {"type": "object", "properties": {}}
    read_only = True

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("数据" * 15000)


class MediumTool(Tool):
    """返回 11000 字符结果的只读工具（低于阈值不触发轻量预防，约 2750 token）。"""

    name = "medium"
    description = "返回中等结果"
    input_schema = {"type": "object", "properties": {}}
    read_only = True

    def execute(self, args: dict) -> ToolResult:
        return ToolResult.success("数据" * 5500)


class FakeProvider:
    """脚本化 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = []  # (messages, tools) 快照

    def stream(self, messages, tools, system):
        self.calls.append((list(messages), list(tools)))
        return iter(self._handler(len(self.calls), messages, tools))


def _agent(tmp_path, provider, window=WINDOW):
    registry = create_registry()
    registry.register(BigTool())
    registry.register(MediumTool())
    session = ChatSession()
    agent = Agent(
        provider, registry, session, context_window=window, context_dir=tmp_path
    )
    return agent, session


def _collect(agent: Agent, text: str) -> list:
    async def go():
        return [event async for event in agent.run(text)]

    return asyncio.run(go())


def _kinds(events: list) -> list:
    return [e.kind for e in events if isinstance(e, CompressEvent)]


def test_prevent_in_loop(tmp_path):
    """轻量预防集成：大结果回灌时存盘、会话留预览（AC3）。"""
    def handler(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="big", args={}), Done()]
        return [PTextDelta("完成"), Done()]

    provider = FakeProvider(handler)
    agent, session = _agent(tmp_path, provider)
    events = _collect(agent, "开始")

    assert "prevent" in _kinds(events)
    stored = [m for m in session.messages if m.tool_results]
    assert stored and "文件路径" in stored[0].tool_results[0].content
    assert "数据" * 15000 not in stored[0].tool_results[0].content
    # 大结果文件已落盘（context_dir 传入 tmp_path）
    assert list(tmp_path.glob("tool_result_*.txt"))
    assert events[-1] == RoundEnd(RoundEndReason.OK)


def test_auto_summarize_no_tools(tmp_path):
    """自动触发：重量兜底摘要，摘要请求无工具、压缩后形态正确（AC5/AC6）。"""
    normal_calls = []

    def handler(n, messages, tools):
        if not tools:
            return [PTextDelta(SUMMARY_REPLY)]  # 摘要调用
        normal_calls.append(n)
        if len(normal_calls) <= 4:
            return [
                ToolCall(id=f"c{n}", name="medium", args={}),
                Done(usage=UsageInfo(input_tokens=190000, output_tokens=10)),
            ]
        return [PTextDelta("完成"), Done()]

    provider = FakeProvider(handler)
    agent, _ = _agent(tmp_path, provider)
    events = _collect(agent, "开始")

    kinds = _kinds(events)
    assert "summarize_start" in kinds and "summarize_done" in kinds
    done_events = [
        e
        for e in events
        if isinstance(e, CompressEvent) and e.kind == "summarize_done"
    ]
    assert any(e.compacted_messages >= 1 for e in done_events)  # 确实压掉了消息
    # 摘要 LLM 请求不带任何工具（AC6）
    summary_calls = [c for c in provider.calls if not c[1]]
    assert summary_calls and all(tools == [] for _, tools in summary_calls)
    # 压缩后的请求形态：用户原文 + 摘要 + 边界 + 保留区
    last_call = provider.calls[-1]
    texts = [m.text for m in last_call[0]]
    assert texts[0] == "开始"  # 用户消息原文保留（N1）
    assert any("集成摘要" in t for t in texts)
    assert any("不是原始对话" in t for t in texts)
    assert events[-1] == RoundEnd(RoundEndReason.OK)


def test_breaker_after_three_failures(tmp_path):
    """摘要连续失败 3 次熔断：出现 breaker 提示，请求照常继续（AC8/N6）。"""
    normal_calls = []

    def handler(n, messages, tools):
        if not tools:
            raise ProviderError("摘要服务不可用")
        normal_calls.append(n)
        if len(normal_calls) <= 6:
            return [
                ToolCall(id=f"c{n}", name="medium", args={}),
                Done(usage=UsageInfo(input_tokens=190000, output_tokens=10)),
            ]
        return [PTextDelta("完成"), Done()]

    provider = FakeProvider(handler)
    agent, _ = _agent(tmp_path, provider)
    events = _collect(agent, "开始")

    kinds = _kinds(events)
    assert kinds.count("summarize_failed") == 3  # 连续 3 次失败
    assert "breaker" in kinds  # 熔断提示
    assert events[-1] == RoundEnd(RoundEndReason.OK)  # 降级后请求照常完成
    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "完成"
    assert agent._compressor.breaker_open


def test_compact_now_manual_success_and_resets_breaker(tmp_path):
    """手动 /compact：无条件执行、熔断后仍可发起、成功解除熔断（F5/F6）。"""
    summary_attempts = {"n": 0}

    def handler(n, messages, tools):
        if not tools:
            summary_attempts["n"] += 1
            if summary_attempts["n"] <= 3:
                raise ProviderError("摘要服务不可用")
            return [PTextDelta(SUMMARY_REPLY)]
        return [PTextDelta("正常"), Done()]

    provider = FakeProvider(handler)
    # 小窗口 + 长消息：手动预算（窗口−3000）装不下全部历史，摘要区非空
    agent, session = _agent(tmp_path, provider, window=20000)
    session.add_user("问题一" * 50)
    for i in range(3):
        session.add_assistant("回答" * 5000)
        session.add_tool_result(ToolResultRecord(str(i), "结果" * 5000, False))
    # 前 3 次失败：反馈中文错误、熔断打开
    for _ in range(3):
        assert "压缩失败" in agent.compact_now()
    assert agent._compressor.breaker_open
    # 熔断后手动仍可发起，第 4 次成功：解除熔断
    feedback = agent.compact_now()
    assert "已压缩" in feedback and "估算节省" in feedback
    assert not agent._compressor.breaker_open
    texts = [m.text for m in session.messages]
    assert any("集成摘要" in t for t in texts)
    assert any("不是原始对话" in t for t in texts)


def test_compact_now_short_session_noop(tmp_path):
    """消息太少（全进保留区）：/compact 返回无需压缩，不调 LLM。"""
    provider = FakeProvider(lambda n, m, t: [PTextDelta("x")])
    agent, session = _agent(tmp_path, provider)
    session.add_user("你好")
    session.add_assistant("你好！")
    assert agent.compact_now() == "上下文较短，无需压缩。"
    assert provider.calls == []
