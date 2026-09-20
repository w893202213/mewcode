"""文件工具：读文件、写文件、改文件（原文唯一匹配替换）。

相对路径以进程工作目录为基准解析（spec N3）；错误信息一律中文、可操作。
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from mewcode.tools.base import Tool, ToolResult


class ReadFileTool(Tool):
    """读取文本文件内容。"""

    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "读取文本文件内容。file_path 为相对或绝对路径；limit 可选，"
        "限制返回的最大行数（从文件开头截取），缺省返回全文。"
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
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return ToolResult.failure(f"读取失败 {path}: {e}")
        limit = args.get("limit")
        if limit is not None:
            lines = text.splitlines()
            if len(lines) > limit:
                text = "\n".join(lines[:limit])
                text += f"\n…（已截断，共 {len(lines)} 行，只显示前 {limit} 行）"
        return ToolResult.success(text)


class WriteFileTool(Tool):
    """写入（创建或覆盖）文本文件。"""

    name: ClassVar[str] = "write_file"
    description: ClassVar[str] = (
        "把 content 写入 file_path 指定的文件（不存在则创建，存在则覆盖），"
        "父目录不存在时自动创建。"
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
    description: ClassVar[str] = (
        "把文件中的 old_text 原文替换为 new_text。old_text 必须在文件中"
        "恰好出现一次（唯一匹配），否则不会修改文件并返回错误。"
        "old_text 不唯一时请提供更长的上下文使其唯一。"
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
