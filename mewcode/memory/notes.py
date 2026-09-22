"""自动笔记：四分类笔记文件管理、索引读写与记忆块拼接（ch11 F5）。

每条笔记一个带 frontmatter 的 Markdown 文件；每级目录一份 index.md
索引（≤200 行 / 25KB）；记忆索引每轮请求前随系统提示注入。
NoteUpdater 在轮自然结束后异步调 LLM 更新（单后台线程 + busy 跳过）。
"""

from __future__ import annotations

import datetime
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from mewcode.memory.prompts import (
    NOTES_SYSTEM_PROMPT,
    NotesParseError,
    build_notes_request,
    parse_notes_response,
)
from mewcode.provider.base import Provider, SystemPrompt, TextDelta
from mewcode.session import Message

# 索引规模上限（超限写盘前截断）
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
# 每条笔记全文进入 LLM 判定时的截断长度（字符）
NOTE_CHARS_LIMIT = 1500
# 合法 slug：小写字母/数字/连字符
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

# 四类笔记类型（frontmatter type 字段取值）
NOTE_TYPES = ("user_preference", "correction", "project_knowledge", "reference")

INDEX_NAME = "index.md"


@dataclass
class NoteChange:
    """LLM 判定的一条笔记变更。"""

    action: str  # "add" | "update" | "none"
    note_type: str  # user_preference | correction | project_knowledge | reference
    scope: str  # "user" | "project"
    name: str  # 短 slug（文件名与索引锚点）
    description: str  # 一句话摘要（索引行用）
    content: str  # 笔记全文


def _slugify(name: str) -> str:
    """LLM 给的 name 不合法时换用时间戳 slug，保证文件名安全。"""
    if _SLUG_RE.match(name):
        return name
    return f"note-{int(time.time())}"


class NoteManager:
    """一级记忆目录（.mewcode/memory 或 ~/.mewcode/memory）的读写。"""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _index_path(self) -> Path:
        return self.directory / INDEX_NAME

    def index_text(self) -> str:
        """索引全文；文件缺失返回空串。"""
        try:
            return self._index_path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def load_notes(self) -> str:
        """全部笔记拼接（每条截断 NOTE_CHARS_LIMIT，供 LLM 判定去重）。"""
        parts: list[str] = []
        for path in sorted(self.directory.glob("*.md")):
            if path.name == INDEX_NAME:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            content = content[:NOTE_CHARS_LIMIT]
            parts.append(f"--- 笔记: {path.stem} ---\n{content}")
        return "\n\n".join(parts)

    def write_note(self, change: NoteChange) -> str:
        """写 <name>.md（frontmatter + 正文）；返回实际文件名 slug。"""
        slug = _slugify(change.name)
        updated = datetime.date.today().isoformat()
        text = (
            "---\n"
            f"name: {slug}\n"
            f"description: {change.description}\n"
            f"type: {change.note_type}\n"
            f"updated: {updated}\n"
            "---\n\n"
            f"{change.content}\n"
        )
        (self.directory / f"{slug}.md").write_text(text, encoding="utf-8")
        return slug

    def write_index(self, text: str) -> None:
        """写 index.md；超 200 行取前 200 行、超 25KB 截到最后一个完整行。"""
        lines = text.splitlines()
        if len(lines) > INDEX_MAX_LINES:
            lines = lines[:INDEX_MAX_LINES]
        data = "\n".join(lines) + "\n"
        if len(data.encode("utf-8")) > INDEX_MAX_BYTES:
            data = data.encode("utf-8")[:INDEX_MAX_BYTES].decode("utf-8", errors="ignore")
            data = data.rsplit("\n", 1)[0] + "\n"
        self._index_path().write_text(data, encoding="utf-8")


def build_memory_block(user_index: str, project_index: str) -> str:
    """拼接记忆块：非空侧拼「## 用户级记忆 / ## 项目级记忆」小节；都为空返回空串。"""
    sections: list[str] = []
    if user_index.strip():
        sections.append(f"## 用户级记忆\n{user_index.strip()}")
    if project_index.strip():
        sections.append(f"## 项目级记忆\n{project_index.strip()}")
    return "\n\n".join(sections)


# 进入 LLM 判定的最近消息条数
RECENT_MESSAGES = 12


class NoteUpdater:
    """轮自然结束后异步更新记忆：单后台 daemon 线程 + busy 跳过。

    provider 调用在后台线程直接执行（httpx.Client 每次调用新建、
    无共享可变状态，与主循环并发安全）。任何异常吞掉并静默降级——
    最多通过 notice 回调发一条提示。
    """

    def __init__(
        self,
        provider: Provider,
        project: NoteManager,
        user: NoteManager,
        notice: Callable[[str], None] | None = None,
    ) -> None:
        self._provider = provider
        self._project = project
        self._user = user
        self._notice = notice
        self._busy = False

    def submit(self, messages: list[Message]) -> bool:
        """提交一轮更新：空闲时取最近消息副本起后台线程；busy 返回 False（跳过）。"""
        if self._busy:
            return False
        self._busy = True
        recent = list(messages[-RECENT_MESSAGES:])
        threading.Thread(
            target=self._run, args=(recent,), daemon=True, name="note-updater"
        ).start()
        return True

    def _run(self, recent: list[Message]) -> None:
        try:
            request = build_notes_request(
                recent,
                self._user.index_text(),
                self._project.index_text(),
                self._user.load_notes(),
                self._project.load_notes(),
            )
            text = self._call_llm(request)
            changes, user_index, project_index = parse_notes_response(text)
            added = updated = 0
            for change in changes:
                if change.action == "none":
                    continue
                # update 找不到目标笔记时按 add 处理（slug 相同自然覆盖）
                manager = self._user if change.scope == "user" else self._project
                manager.write_note(change)
                if change.action == "add":
                    added += 1
                else:
                    updated += 1
            self._user.write_index(user_index)
            self._project.write_index(project_index)
            self._notify(f"📝 记忆已更新：{added} 条新增 / {updated} 条更新")
        except Exception:
            # ProviderError / NotesParseError / OSError 等全部静默降级
            self._notify("记忆更新失败，本轮跳过")
        finally:
            self._busy = False

    def _call_llm(self, request: str) -> str:
        """同步调 LLM 收集完整回复文本（无工具、固定系统提示）。"""
        system = SystemPrompt(stable=NOTES_SYSTEM_PROMPT, dynamic="")
        parts: list[str] = []
        for event in self._provider.stream(
            [Message(role="user", text=request)], [], system
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
        return "".join(parts)

    def _notify(self, text: str) -> None:
        if self._notice is not None:
            try:
                self._notice(text)
            except Exception:
                pass  # 提示通道失败不影响更新本身
