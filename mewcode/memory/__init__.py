"""memory 包：记忆系统（ch11）——项目指令、会话存档、自动笔记。

对外暴露装配入口 build_memory_context：CLI 启动时创建/恢复会话、
清理过期存档、构造记忆索引加载器；TUI 消费 MemoryContext 继续工作。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mewcode.memory.instructions import InstructionResult, load_instructions
from mewcode.memory.notes import (
    NoteChange,
    NoteManager,
    NoteUpdater,
    build_memory_block,
)
from mewcode.memory.sessions import (
    SessionLog,
    SessionStore,
    SessionSummary,
    maybe_add_time_gap_notice,
)
from mewcode.session import ChatSession

# 项目级记忆目录（相对项目根）
PROJECT_MEMORY_DIR = Path(".mewcode") / "memory"
# 项目级存档目录（相对项目根）
PROJECT_SESSIONS_DIR = Path(".mewcode") / "sessions"
# 用户级记忆目录（相对用户目录）
USER_MEMORY_DIR = Path("memory")


@dataclass
class MemoryContext:
    """记忆系统装配产物：存档句柄、已恢复（或空）的会话与索引加载器。"""

    store: SessionStore  # 存档目录（/resume 列表与落盘共用）
    log: SessionLog  # 当前会话日志（新建或恢复目标，继续追加）
    session: ChatSession  # 已恢复或为空的会话
    resume_summary: str  # 恢复/清理信息（欢迎面板展示；新会话可能含清理提示）
    memory_loader: Callable[[], str]  # 每轮现读两份 index.md 拼文本


def build_memory_context(
    project_root: Path, user_dir: Path, resume: str | None
) -> MemoryContext:
    """装配记忆上下文。

    resume: None=新会话；""=恢复最近会话；"<ID>"=恢复指定会话。
    恢复失败（文件不存在/无有效记录）降级为新会话并附提示文本。
    """
    store = SessionStore(project_root / PROJECT_SESSIONS_DIR)
    notes: list[str] = []
    cleaned = store.cleanup_expired()
    if cleaned:
        notes.append(f"已清理 {cleaned} 个过期会话存档。")

    session_id: str | None = resume if resume else None
    if resume == "":
        recent = store.list_recent(1)
        if recent:
            session_id = recent[0].id

    session = ChatSession()
    log: SessionLog | None = None
    if session_id is not None:
        try:
            result = store.load(session_id)
        except FileNotFoundError:
            notes.append(f"会话 {session_id} 不存在，已按新会话继续。")
        else:
            if not result.messages:
                notes.append(f"会话 {session_id} 无有效记录，已按新会话继续。")
            else:
                session.restore(result.messages)
                if maybe_add_time_gap_notice(session.messages, result.last_ts, time.time()):
                    notes.append("该会话已超过 7 天，已插入时间跨度提醒。")
                log = SessionLog(store.path(session_id))
                title = result.title or "（无标题）"
                notes.append(
                    f"已恢复会话 {session_id}（{title}，{len(result.messages)} 条消息）。"
                )
                if result.skipped_lines:
                    notes.append(f"跳过 {result.skipped_lines} 行无法解析的记录。")
                if result.truncated:
                    notes.append("发现未写完的对话尾巴，已截断至最后一个完整轮次。")
    if log is None:
        new_id, log = store.new_log()
        notes.insert(0, f"新会话 {new_id}。")

    def memory_loader() -> str:
        return build_memory_block(
            _read_index(user_dir / USER_MEMORY_DIR),
            _read_index(project_root / PROJECT_MEMORY_DIR),
        )

    return MemoryContext(
        store=store,
        log=log,
        session=session,
        resume_summary=" ".join(n for n in notes if n),
        memory_loader=memory_loader,
    )


def _read_index(directory: Path) -> str:
    """读索引文件；缺失/损坏返回空串（记忆损坏不阻断启动，N2）。"""
    try:
        return (directory / "index.md").read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, UnicodeDecodeError):
        return ""


__all__ = [
    "MemoryContext",
    "build_memory_context",
    "InstructionResult",
    "load_instructions",
    "NoteChange",
    "NoteManager",
    "NoteUpdater",
    "build_memory_block",
    "SessionLog",
    "SessionStore",
    "SessionSummary",
    "maybe_add_time_gap_notice",
    "PROJECT_MEMORY_DIR",
    "PROJECT_SESSIONS_DIR",
    "USER_MEMORY_DIR",
]
