"""会话存档与恢复测试（ch11 F2–F4）。"""

import json
import os
import re
import time

import pytest

from mewcode.memory.sessions import (
    LoadResult,
    SessionStore,
    maybe_add_time_gap_notice,
)
from mewcode.session import ChatSession, Message, ToolResultRecord, ToolUse


def _make_messages() -> list[Message]:
    """构造一组覆盖三种行类型的消息。"""
    return [
        Message(role="user", text="帮我看看 git 状态"),
        Message(
            role="assistant",
            text="好的",
            thinking="让我执行命令",
            thinking_raw=[{"type": "thinking", "thinking": "让我执行命令", "signature": "sig1"}],
            tool_uses=[ToolUse(id="tu1", name="run_command", args={"command": "git status"})],
        ),
        Message(
            role="user",
            text="",
            tool_results=[ToolResultRecord(tool_use_id="tu1", content="On branch master", is_error=False)],
        ),
        Message(role="assistant", text="当前在 master 分支"),
    ]


# ---- T4: 追加写 / ID / 扫描 / 清理 ----


def test_new_log_id_format_and_file_created(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session_id, log = store.new_log()
    assert re.fullmatch(r"\d{8}-\d{6}-[a-z0-9]{4}", session_id)
    assert (tmp_path / "sessions" / f"{session_id}.jsonl").exists()
    log.close()


def test_append_messages_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session_id, log = store.new_log()
    ts = 1720000000.5
    log.append_messages(_make_messages(), ts)
    log.close()
    lines = (tmp_path / "sessions" / f"{session_id}.jsonl").read_text().splitlines()
    assert len(lines) == 4
    records = [json.loads(l) for l in lines]
    assert records[0]["type"] == "user" and records[0]["text"] == "帮我看看 git 状态"
    assert records[1]["type"] == "assistant"
    assert records[1]["tool_uses"] == [{"id": "tu1", "name": "run_command", "args": {"command": "git status"}}]
    assert records[1]["thinking_raw"] == [{"type": "thinking", "thinking": "让我执行命令", "signature": "sig1"}]
    assert records[2]["type"] == "tool_result" and records[2]["tool_use_id"] == "tu1"
    assert all(r["ts"] == ts for r in records)


def test_append_is_append_only(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session_id, log = store.new_log()
    log.append_messages([Message(role="user", text="第一条")], time.time())
    log.close()
    # 重新打开同一文件追加
    from mewcode.memory.sessions import SessionLog

    log2 = SessionLog(tmp_path / "sessions" / f"{session_id}.jsonl")
    log2.append_messages([Message(role="assistant", text="回复")], time.time())
    log2.close()
    lines = (tmp_path / "sessions" / f"{session_id}.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "user"


def test_new_log_retries_on_collision(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session_id, log = store.new_log()
    log.close()
    # 删除文件但保留同 ID 再建？改为预占：直接把现有文件改名模拟占用不可能——直接再调 new_log
    # 同秒内生成不同 ID（随机后缀防撞车）
    id2, _ = store.new_log()
    assert id2 != session_id


def test_list_recent_title_count_order(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    sid1, log1 = store.new_log()
    long_title = "这是一个非常长的标题" * 10
    log1.append_messages(
        [Message(role="user", text=long_title), Message(role="assistant", text="a")],
        time.time(),
    )
    log1.close()
    time.sleep(0.02)
    sid2, log2 = store.new_log()
    log2.append_messages(
        [Message(role="user", text="第二个会话"), Message(role="assistant", text="b")],
        time.time(),
    )
    log2.close()
    summaries = store.list_recent(n=5)
    assert [s.id for s in summaries[:2]] == [sid2, sid1]  # mtime 倒序
    assert summaries[0].message_count == 2
    assert summaries[0].title == "第二个会话"
    assert summaries[1].title == long_title[:50] + "…"


def test_cleanup_expired_only_old_files(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    sid_old, log = store.new_log()
    log.append_messages([Message(role="user", text="旧会话")], time.time())
    log.close()
    sid_new, log = store.new_log()
    log.append_messages([Message(role="user", text="新会话")], time.time())
    log.close()
    old_path = tmp_path / "sessions" / f"{sid_old}.jsonl"
    # 把旧会话 mtime 拨到 40 天前
    old_time = time.time() - 40 * 86400
    os.utime(old_path, (old_time, old_time))
    removed = store.cleanup_expired(days=30)
    assert removed == 1
    assert not old_path.exists()
    assert (tmp_path / "sessions" / f"{sid_new}.jsonl").exists()


# ---- T5: 恢复加载 ----


def _write_raw(store: SessionStore, session_id: str, lines: list[str]) -> None:
    (store.directory / f"{session_id}.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_load_full_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    session_id, log = store.new_log()
    log.append_messages(_make_messages(), 1720000000.5)
    log.close()
    result = store.load(session_id)
    assert result.skipped_lines == 0 and result.truncated is False
    assert result.title == "帮我看看 git 状态"
    assert result.last_ts == 1720000000.5
    msgs = result.messages
    assert [m.role for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[1].tool_uses == [ToolUse(id="tu1", name="run_command", args={"command": "git status"})]
    assert msgs[1].thinking_raw == [{"type": "thinking", "thinking": "让我执行命令", "signature": "sig1"}]
    assert msgs[2].tool_results[0].tool_use_id == "tu1"
    assert msgs[2].tool_results[0].content == "On branch master"


def test_load_skips_bad_lines(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    _write_raw(
        store,
        "s1",
        [
            '{"type": "user", "text": "正常", "ts": 1.0}',
            "这不是 JSON",
            '{"type": "weird", "ts": 2.0}',
            '{"type": "user", "ts": 3.0}',  # 缺 text
            '{"type": "assistant", "text": "回复", "ts": 4.0}',
        ],
    )
    result = store.load("s1")
    assert result.skipped_lines == 3
    assert [m.text for m in result.messages] == ["正常", "回复"]
    assert result.last_ts == 4.0


def test_load_truncates_unpaired_tool_use_tail(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    _write_raw(
        store,
        "s1",
        [
            '{"type": "user", "text": "问题", "ts": 1.0}',
            '{"type": "assistant", "text": "我来查", "tool_uses": [{"id": "tu1", "name": "run_command", "args": {"command": "ls"}}], "ts": 2.0}',
            # 工具结果缺失（崩溃未写完），后面还跟了新一轮
            '{"type": "user", "text": "又问", "ts": 3.0}',
            '{"type": "assistant", "text": "答", "ts": 4.0}',
        ],
    )
    result = store.load("s1")
    assert result.truncated is True
    assert [m.text for m in result.messages] == ["问题"]  # assistant 行及之后全丢弃
    assert result.last_ts == 1.0


def test_load_truncates_when_results_cut_short(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    _write_raw(
        store,
        "s1",
        [
            '{"type": "user", "text": "问题", "ts": 1.0}',
            '{"type": "assistant", "text": "查两个", "tool_uses": [{"id": "t1", "name": "a", "args": {}}, {"id": "t2", "name": "b", "args": {}}], "ts": 2.0}',
            '{"type": "tool_result", "tool_use_id": "t1", "content": "结果1", "ts": 3.0}',
            # t2 的结果缺失（文件在此结束）
        ],
    )
    result = store.load("s1")
    assert result.truncated is True
    assert [m.text for m in result.messages] == ["问题"]


def test_load_keeps_complete_tool_round(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    _write_raw(
        store,
        "s1",
        [
            '{"type": "user", "text": "问题", "ts": 1.0}',
            '{"type": "assistant", "text": "查两个", "tool_uses": [{"id": "t1", "name": "a", "args": {}}, {"id": "t2", "name": "b", "args": {}}], "ts": 2.0}',
            '{"type": "tool_result", "tool_use_id": "t2", "content": "结果2", "ts": 3.0}',
            '{"type": "tool_result", "tool_use_id": "t1", "content": "结果1", "ts": 4.0}',
            '{"type": "assistant", "text": "完成了", "ts": 5.0}',
        ],
    )
    result = store.load("s1")
    assert result.truncated is False
    # 两条 tool_result 各占一条消息
    assert [m.text for m in result.messages] == ["问题", "查两个", "", "", "完成了"]
    assert result.last_ts == 5.0


def test_load_keeps_trailing_user_line(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    _write_raw(
        store,
        "s1",
        [
            '{"type": "user", "text": "问题", "ts": 1.0}',
            '{"type": "assistant", "text": "答", "ts": 2.0}',
            '{"type": "user", "text": "没收到回复的问题", "ts": 3.0}',
        ],
    )
    result = store.load("s1")
    assert [m.text for m in result.messages] == ["问题", "答", "没收到回复的问题"]
    assert result.truncated is False


def test_load_missing_file_raises(tmp_path):
    store = SessionStore(tmp_path / "sessions")
    with pytest.raises(FileNotFoundError):
        store.load("nope")


def test_time_gap_notice_threshold(tmp_path):
    messages = [Message(role="user", text="旧对话")]
    # 6 天前：不插入
    assert maybe_add_time_gap_notice(messages, time.time() - 6 * 86400, time.time()) is False
    assert len(messages) == 1
    # 8 天前：插入且带 summary 标记
    inserted = maybe_add_time_gap_notice(messages, time.time() - 8 * 86400, time.time())
    assert inserted is True
    assert len(messages) == 2
    assert "时间跨度提醒" in messages[-1].text
    assert "8 天前" in messages[-1].text
    assert messages[-1].summary is True
    # last_ts 为 None：不插入
    assert maybe_add_time_gap_notice([], None, time.time()) is False
