"""会话存档：JSONL 追加写、恢复加载、扫描现算与过期清理（ch11 F2–F4）。

每行一个 JSON 对象，type 三选一：user / assistant / tool_result；
ts 为 time.time() 浮点。不维护独立 meta 文件——标题、消息数等
需要时扫 JSONL 现算。追加写 + flush：崩溃最多只丢最后一行。
"""

from __future__ import annotations

import datetime
import json
import os
import random
import string
import time
from dataclasses import dataclass
from pathlib import Path

from mewcode.session import Message, ToolResultRecord, ToolUse

# 会话 ID 随机后缀字符集（4 位防同秒撞车）
_ID_CHARS = string.ascii_lowercase + string.digits
# 时间跨度提醒阈值（天）
GAP_NOTICE_DAYS = 7
# 过期清理阈值（天）
EXPIRED_DAYS = 30
# 标题截断长度（字符）
TITLE_CHARS = 50


def message_to_line(m: Message, ts: float) -> dict:
    """一条会话消息 → JSONL 行对象。tool_result 每条记录一行。"""
    if m.tool_results:
        record = m.tool_results[0]
        return {
            "type": "tool_result",
            "tool_use_id": record.tool_use_id,
            "content": record.content,
            "is_error": record.is_error,
            "ts": ts,
        }
    if m.role == "user":
        return {"type": "user", "text": m.text, "summary": m.summary, "ts": ts}
    return {
        "type": "assistant",
        "text": m.text,
        "thinking": m.thinking,
        "thinking_raw": m.thinking_raw,
        "tool_uses": [{"id": u.id, "name": u.name, "args": u.args} for u in m.tool_uses],
        "summary": m.summary,
        "ts": ts,
    }


@dataclass
class SessionSummary:
    """一个存档会话的摘要（扫描现算，/resume 列表用）。"""

    id: str
    title: str
    message_count: int
    mtime: float


class SessionLog:
    """单个会话的 JSONL 追加写句柄。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # a+ 追加模式打开（不截断，可读）；文件非空且末尾无换行（手工
        # 编辑或崩溃尾巴）时先补一个换行——否则新行与残行粘连成坏行丢数据
        self._file = path.open("a+", encoding="utf-8")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() > 0:
            self._file.seek(self._file.tell() - 1)
            if self._file.read(1) != "\n":
                self._file.write("\n")

    def append_messages(self, messages: list[Message], ts: float) -> None:
        """逐条消息写一行 JSON 并 flush（崩溃最多丢最后一行）。"""
        for m in messages:
            self._file.write(
                json.dumps(message_to_line(m, ts), ensure_ascii=False) + "\n"
            )
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def _parse_line(line: str) -> Message | None:
    """一行 JSON → Message；坏行（解析失败/type 未知/关键字段缺失）返回 None。"""
    try:
        data = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    if kind == "user":
        text = data.get("text")
        if not isinstance(text, str):
            return None
        return Message(
            role="user", text=text, summary=bool(data.get("summary", False))
        )
    if kind == "assistant":
        text = data.get("text")
        if not isinstance(text, str):
            return None
        thinking = data.get("thinking")
        thinking_raw = data.get("thinking_raw")
        tool_uses_raw = data.get("tool_uses")
        tool_uses: list[ToolUse] = []
        if isinstance(tool_uses_raw, list):
            for u in tool_uses_raw:
                if (
                    isinstance(u, dict)
                    and isinstance(u.get("id"), str)
                    and isinstance(u.get("name"), str)
                    and isinstance(u.get("args"), dict)
                ):
                    tool_uses.append(ToolUse(id=u["id"], name=u["name"], args=u["args"]))
        return Message(
            role="assistant",
            text=text,
            thinking=thinking if isinstance(thinking, str) else "",
            thinking_raw=thinking_raw if isinstance(thinking_raw, list) else [],
            tool_uses=tool_uses,
            summary=bool(data.get("summary", False)),
        )
    if kind == "tool_result":
        tool_use_id = data.get("tool_use_id")
        content = data.get("content")
        if not isinstance(tool_use_id, str) or not isinstance(content, str):
            return None
        return Message(
            role="user",
            text="",
            tool_results=[
                ToolResultRecord(
                    tool_use_id=tool_use_id,
                    content=content,
                    is_error=bool(data.get("is_error", False)),
                )
            ],
        )
    return None


@dataclass
class LoadResult:
    """恢复加载结果。"""

    messages: list[Message]  # 截断后的有效消息
    skipped_lines: int  # 跳过的坏行数
    truncated: bool  # 是否发生过截断
    last_ts: float | None  # 最后一条消息时间戳
    title: str  # 首条用户消息截断 50 字符


class SessionStore:
    """sessions 目录管理：ID 生成、恢复、扫描现算、过期清理。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def path(self, session_id: str) -> Path:
        """会话 ID → 存档文件路径。"""
        return self.directory / f"{session_id}.jsonl"

    def new_log(self) -> tuple[str, SessionLog]:
        """生成新会话 ID 并打开追加写句柄；撞车重试。"""
        for _ in range(10):
            suffix = "".join(random.choices(_ID_CHARS, k=4))
            session_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{suffix}"
            path = self.path(session_id)
            if not path.exists():
                return session_id, SessionLog(path)
        raise RuntimeError("无法生成不冲突的会话 ID")

    def load(self, session_id: str) -> LoadResult:
        """恢复加载：坏行跳过、未配平工具调用截断、现算标题与时间。

        先解析收集（消息, ts）记录，再在记录层做配平截断，最后统一
        折算 messages 与 last_ts——截断回滚时时间自然跟着回滚。
        """
        path = self.path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"会话存档不存在: {path}")
        records: list[tuple[Message, float | None]] = []
        skipped = 0
        truncated = False
        # 配平状态：上一 assistant 行带 tool_uses 后，等待结果补齐的 id 集合；
        # pending_marker = 该 assistant 行在 records 中的下标（截断回退点）
        pending_ids: set[str] | None = None
        pending_marker = 0
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            msg = _parse_line(line)
            if msg is None:
                skipped += 1
                continue
            try:
                ts_field = json.loads(line).get("ts")
            except (json.JSONDecodeError, TypeError):
                ts_field = None
            ts = float(ts_field) if isinstance(ts_field, (int, float)) else None
            if pending_ids is not None:
                # 上一 assistant 的工具调用还在等结果：只接受 tool_result 行
                if msg.tool_results:
                    pending_ids.discard(msg.tool_results[0].tool_use_id)
                    if not pending_ids:
                        pending_ids = None
                    records.append((msg, ts))
                else:
                    # 遇到非结果行仍未配平 → 崩溃尾巴，丢弃 assistant 行及之后
                    truncated = True
                    records = records[:pending_marker]
                    break
            else:
                if msg.role == "assistant" and msg.tool_uses:
                    pending_ids = {u.id for u in msg.tool_uses}
                    pending_marker = len(records)
                records.append((msg, ts))
        # 文件在工具结果中途结束仍未配平 → 同样截断
        if pending_ids is not None:
            truncated = True
            records = records[:pending_marker]
        messages = [m for m, _ in records]
        last_ts = next((ts for _, ts in reversed(records) if ts is not None), None)
        title = ""
        for m in messages:
            if m.role == "user" and m.text and not m.tool_results:
                title = m.text
                break
        if title:
            title = title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")
        return LoadResult(
            messages=messages,
            skipped_lines=skipped,
            truncated=truncated,
            last_ts=last_ts,
            title=title,
        )

    def list_recent(self, n: int = 5) -> list[SessionSummary]:
        """扫目录现算摘要：标题（首条用户消息）、消息数、mtime，倒序取前 n。"""
        summaries: list[SessionSummary] = []
        for path in self.directory.glob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
                title = ""
                count = 0
                for line in path.read_text(encoding="utf-8").splitlines():
                    msg = _parse_line(line)
                    if msg is None:
                        continue
                    count += 1
                    if title == "" and msg.role == "user" and msg.text and not msg.tool_results:
                        title = msg.text
            except (OSError, UnicodeDecodeError):
                continue
            if title:
                title = title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")
            summaries.append(
                SessionSummary(
                    id=path.stem, title=title or "（无标题）", message_count=count, mtime=mtime
                )
            )
        summaries.sort(key=lambda s: s.mtime, reverse=True)
        return summaries[:n]

    def cleanup_expired(self, days: int = EXPIRED_DAYS) -> int:
        """删除 mtime 早于 days 天的会话文件，返回删除数。"""
        deadline = time.time() - days * 86400
        removed = 0
        for path in self.directory.glob("*.jsonl"):
            try:
                if path.stat().st_mtime < deadline:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


def maybe_add_time_gap_notice(
    messages: list[Message], last_ts: float | None, now: float, days: int = GAP_NOTICE_DAYS
) -> bool:
    """最后活动距今超过 days 天：追加时间跨度提醒消息（summary=True），返回是否插入。"""
    if last_ts is None or now - last_ts <= days * 86400:
        return False
    last_date = datetime.datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M")
    today = datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
    gap_days = int((now - last_ts) // 86400)
    messages.append(
        Message(
            role="user",
            text=(
                f"【时间跨度提醒】以上对话内容来自 {gap_days} 天前的会话存档"
                f"（最后活动时间 {last_date}）。当前时间已变为 {today}，"
                "请结合最新时间与当前状态继续。"
            ),
            summary=True,
        )
    )
    return True
