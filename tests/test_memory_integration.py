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


# ---- T11: MemoryContext 装配 ----

import json
import os
import time


def _ctx(tmp_path, resume=None):
    from mewcode.memory import build_memory_context

    return build_memory_context(tmp_path / "proj", tmp_path / "home", resume)


def _write_session_file(tmp_path, session_id, lines):
    sessions = tmp_path / "proj" / ".mewcode" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_build_context_new_session(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.session.messages == []
    assert "新会话" in ctx.resume_summary
    assert ctx.log.path.name.endswith(".jsonl")
    assert ctx.log.path.exists()


def test_build_context_resume_by_id(tmp_path):
    _write_session_file(
        tmp_path,
        "20260922-101010-abcd",
        [
            '{"type": "user", "text": "旧问题", "ts": %f}' % (time.time() - 60),
            '{"type": "assistant", "text": "旧回答", "ts": %f}' % (time.time() - 59),
        ],
    )
    ctx = _ctx(tmp_path, resume="20260922-101010-abcd")
    assert [m.text for m in ctx.session.messages] == ["旧问题", "旧回答"]
    assert "已恢复会话 20260922-101010-abcd" in ctx.resume_summary
    assert "2 条消息" in ctx.resume_summary
    assert ctx.log.path.name == "20260922-101010-abcd.jsonl"
    # 恢复后继续追加同一文件
    ctx.log.append_messages([Message(role="user", text="新消息")], time.time())
    ctx.log.close()
    lines = ctx.log.path.read_text().splitlines()
    assert len(lines) == 3


def test_build_context_resume_recent_when_empty_arg(tmp_path):
    _write_session_file(
        tmp_path,
        "20260922-101010-aaaa",
        ['{"type": "user", "text": "最近的问题", "ts": %f}' % (time.time() - 30)],
    )
    ctx = _ctx(tmp_path, resume="")
    assert ctx.session.messages[0].text == "最近的问题"
    assert "已恢复会话 20260922-101010-aaaa" in ctx.resume_summary


def test_build_context_resume_inserts_gap_notice(tmp_path):
    _write_session_file(
        tmp_path,
        "20260922-101010-bbbb",
        ['{"type": "user", "text": "8 天前的问题", "ts": %f}' % (time.time() - 8 * 86400)],
    )
    ctx = _ctx(tmp_path, resume="20260922-101010-bbbb")
    assert len(ctx.session.messages) == 2  # 原消息 + 时间跨度提醒
    assert "时间跨度提醒" in ctx.session.messages[-1].text
    assert "时间跨度提醒" in ctx.resume_summary


def test_build_context_resume_invalid_id_falls_back(tmp_path):
    ctx = _ctx(tmp_path, resume="20260922-000000-zzzz")
    assert ctx.session.messages == []
    assert "不存在" in ctx.resume_summary
    assert "新会话" in ctx.resume_summary


def test_build_context_resume_empty_file_falls_back(tmp_path):
    _write_session_file(tmp_path, "20260922-101010-cccc", ["坏行不是 JSON"])
    ctx = _ctx(tmp_path, resume="20260922-101010-cccc")
    assert ctx.session.messages == []
    assert "无有效记录" in ctx.resume_summary


def test_build_context_cleanup_expired_noted(tmp_path):
    _write_session_file(tmp_path, "20260922-101010-old1", ['{"type": "user", "text": "旧", "ts": 1.0}'])
    old_path = tmp_path / "proj" / ".mewcode" / "sessions" / "20260922-101010-old1.jsonl"
    old_time = time.time() - 40 * 86400
    os.utime(old_path, (old_time, old_time))
    ctx = _ctx(tmp_path)
    assert "已清理 1 个过期会话存档" in ctx.resume_summary
    assert not old_path.exists()


def test_memory_loader_reads_indexes(tmp_path):
    user_mem = tmp_path / "home" / "memory"
    proj_mem = tmp_path / "proj" / ".mewcode" / "memory"
    user_mem.mkdir(parents=True)
    proj_mem.mkdir(parents=True)
    (user_mem / "index.md").write_text("- [a](a.md) — 用户记忆", encoding="utf-8")
    (proj_mem / "index.md").write_text("- [b](b.md) — 项目记忆", encoding="utf-8")
    ctx = _ctx(tmp_path)
    text = ctx.memory_loader()
    assert "## 用户级记忆" in text and "用户记忆" in text
    assert "## 项目级记忆" in text and "项目记忆" in text


def test_memory_loader_missing_indexes_returns_empty(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.memory_loader() == ""
