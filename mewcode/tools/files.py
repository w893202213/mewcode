"""文件工具：读文件、写文件、改文件（原文唯一匹配替换）。

相对路径以进程工作目录为基准解析（spec N3）；错误信息一律中文、可操作。
read_file 支持进程内读缓存（ch16 F5）：路径 + mtime 键控，缓存实例经
ParentContext 注入（归属 Agent 实例，父与子互不可见）。
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from mewcode.agents.context import current
from mewcode.tools.base import Tool, ToolResult


class FileReadCache:
    """read_file 的进程内读缓存：dict 以路径为键存 (mtime, content)。

    命中 = 键存在且 mtime 一致——文件修改（mtime 变）自然失效，
    无需写失效通知；进程内有效（ch16 F5）。
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}

    def get(self, path: str, mtime: float) -> str | None:
        """命中返回缓存内容；键缺失或 mtime 不一致返回 None。"""
        hit = self._store.get(path)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        return None

    def put(self, path: str, mtime: float, content: str) -> None:
        self._store[path] = (mtime, content)


def _current_cache() -> FileReadCache | None:
    """取当前线程的读缓存实例（经 ParentContext 注入）；无上下文为 None。"""
    ctx = current()
    return ctx.read_cache if ctx is not None else None


class ReadFileTool(Tool):
    """读取文本文件内容。"""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "读取文本文件内容。file_path 为相对或绝对路径；limit 可选，"
        "限制返回的最大行数（从文件开头截取），缺省返回全文。"
        "读文件请用本工具，不要用 shell 的 cat/sed。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要读取的文件路径"},
            "limit": {"type": "integer", "description": "最大行数（可选）"},
        },
        "required": ["file_path"],
    }

    def execute(self, args: dict) -> ToolResult:
        path = Path(args["file_path"])
        if not path.is_file():
            return ToolResult.failure(f"文件不存在: {path}")
        try:
            mtime = path.stat().st_mtime
        except OSError as e:
            return ToolResult.failure(f"读取失败 {path}: {e}")
        # ch16 F5：读缓存——命中直接返回缓存内容（limit 截断仍在取回后应用，
        # 不同 limit 的请求共享同一份全文缓存）
        cache = _current_cache()
        if cache is not None:
            cached = cache.get(str(path), mtime)
            if cached is not None:
                return ToolResult.success(self._apply_limit(cached, args))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return ToolResult.failure(f"读取失败 {path}: {e}")
        if cache is not None:
            cache.put(str(path), mtime, text)
        return ToolResult.success(self._apply_limit(text, args))

    @staticmethod
    def _apply_limit(text: str, args: dict) -> str:
        """limit 截断（缓存命中与直读共用）。"""
        limit = args.get("limit")
        if limit is None:
            return text
        lines = text.splitlines()
        if len(lines) > limit:
            truncated = "\n".join(lines[:limit])
            return (
                truncated
                + f"\n…（已截断，共 {len(lines)} 行，只显示前 {limit} 行）"
            )
        return text


class WriteFileTool(Tool):
    """写入（创建或覆盖）文本文件。"""

    name: ClassVar[str] = "write_file"
    read_only: ClassVar[bool] = False  # 修改文件系统：有副作用，plan 模式不可用
    description: ClassVar[str] = (
        "把 content 写入 file_path 指定的文件（不存在则创建，存在则覆盖），"
        "父目录不存在时自动创建。覆盖写入前请先确认目标文件的当前内容。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "目标文件路径"},
            "content": {"type": "string", "description": "完整文件内容"},
        },
        "required": ["file_path", "content"],
    }

    def execute(self, args: dict) -> ToolResult:
        path = Path(args["file_path"])
        content = args["content"]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            return ToolResult.failure(f"写入失败 {path}: {e}")
        size = len(content.encode("utf-8"))
        return ToolResult.success(f"已写入 {path}（{size} 字节）")


class EditFileTool(Tool):
    """按原文唯一匹配替换文件内容。"""

    name: ClassVar[str] = "edit_file"
    read_only: ClassVar[bool] = False  # 修改文件系统：有副作用，plan 模式不可用
    description: ClassVar[str] = (
        "把文件中的 old_text 原文替换为 new_text。old_text 必须在文件中"
        "恰好出现一次（唯一匹配），否则不会修改文件并返回错误。"
        "old_text 不唯一时请提供更长的上下文使其唯一。"
        "修改文件前必须先用 read_file 读取该文件当前内容，确保 old_text 与文件一致。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "要修改的文件路径"},
            "old_text": {"type": "string", "description": "要被替换的原文（唯一匹配）"},
            "new_text": {"type": "string", "description": "替换后的新文本"},
        },
        "required": ["file_path", "old_text", "new_text"],
    }

    def execute(self, args: dict) -> ToolResult:
        path = Path(args["file_path"])
        old_text = args["old_text"]
        new_text = args["new_text"]
        if not old_text:
            return ToolResult.failure("old_text 不能为空")
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ToolResult.failure(f"文件不存在: {path}")
        except (OSError, UnicodeDecodeError) as e:
            return ToolResult.failure(f"读取失败 {path}: {e}")

        count = content.count(old_text)
        if count == 0:
            return ToolResult.failure(
                f"原文未找到: 文件 {path} 中不存在 old_text 的内容，"
                "请先用 read_file 确认文件当前内容"
            )
        if count > 1:
            line_numbers = [
                i + 1
                for i, line in enumerate(content.splitlines(keepends=True))
                if old_text in line
            ]
            lines_hint = f"（出现于第 {', '.join(str(n) for n in line_numbers[:10])} 行）" if line_numbers else ""
            return ToolResult.failure(
                f"原文匹配 {count} 次，不唯一{lines_hint}，"
                "请提供更长的上下文（含更多相邻行）使 old_text 唯一，不要修改文件"
            )

        # 唯一匹配：替换并写回
        new_content = content.replace(old_text, new_text, 1)
        pos = new_content.find(new_text)
        line_number = new_content[:pos].count("\n") + 1
        try:
            path.write_text(new_content, encoding="utf-8")
        except OSError as e:
            return ToolResult.failure(f"写入失败 {path}: {e}")
        return ToolResult.success(f"已替换 {path} 第 {line_number} 行的唯一匹配内容")
