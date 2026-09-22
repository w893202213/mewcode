"""记忆系统集成测试（ch11）：Agent 恢复压缩入口、MemoryContext 装配、记忆注入。"""

from pathlib import Path

from mewcode.agent.loop import Agent
from mewcode.provider.base import TextDelta
from mewcode.session import ChatSession, Message
from mewcode.tools import create_registry

# 与 test_compressor.py 同款的摘要响应（解析只取正式摘要段）
SUMMARY_REPLY = "<分析草稿>\n草稿\n</分析草稿>\n<正式摘要>\n## 总体目标与状态\n测试摘要\n</正式摘要>"


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0

    def stream(self, messages, tools, system):
        self.calls += 1
        return iter(self._handler(self.calls))


def _long_session(n_rounds: int = 8) -> ChatSession:
    """构造多轮长对话（每轮一条长用户消息 + 助手回复）。

    单条 ≈4800 字符 ≈1200 token，总量远超自动保留区预算 10000 token，
    保证有消息进入摘要区。
    """
    session = ChatSession()
    for i in range(n_rounds):
        session.add_user(f"第 {i} 轮问题，" + "内容很长的提问。" * 600)
        session.add_assistant(f"第 {i} 轮回答，" + "内容很长的回复。" * 600)
    return session


def _agent(tmp_path, provider, session, context_window):
    return Agent(
        provider,
        create_registry(),
        session,
        context_window=context_window,
        context_dir=tmp_path / "context",
    )


# ---- T10: maybe_compact_on_restore ----


def test_maybe_compact_on_restore_noop_when_short():
    agent = _agent(Path("/tmp"), FakeProvider(lambda n: []), ChatSession(), 200000)
    assert agent.maybe_compact_on_restore() is None


def test_maybe_compact_on_restore_compacts_overlong_session(tmp_path):
    session = _long_session()
    before = len(session.messages)
    provider = FakeProvider(lambda n: [TextDelta(SUMMARY_REPLY)])
    agent = _agent(tmp_path, provider, session, context_window=5000)
    feedback = agent.maybe_compact_on_restore()
    assert feedback is not None
    assert "已先压缩一次" in feedback
    assert len(session.messages) < before  # 摘要区被压掉
    assert any(m.summary for m in session.messages)  # 摘要/边界消息带标记


def test_maybe_compact_on_restore_failure_returns_error_text(tmp_path):
    from mewcode.provider.base import ProviderError

    def fail(n):
        raise ProviderError("网络错误")

    session = _long_session()
    agent = _agent(tmp_path, FakeProvider(fail), session, context_window=5000)
    feedback = agent.maybe_compact_on_restore()
    assert feedback is not None
    assert feedback.startswith("恢复后压缩失败")


def test_agent_memory_loader_injected_into_system_prompt():
    systems: list = []

    class RecordingProvider:
        def stream(self, messages, tools, system):
            systems.append(system)
            return iter([])

    session = ChatSession()
    session.add_user("你好")
    agent = Agent(
        RecordingProvider(),
        create_registry(),
        session,
        memory_loader=lambda: "## 用户级记忆\n- [a](a.md)",
    )

    import asyncio

    async def go():
        return [event async for event in agent.run("新问题")]

    asyncio.run(go())
    assert any("【记忆】" in s.dynamic for s in systems)
    assert any("- [a](a.md)" in s.dynamic for s in systems)
