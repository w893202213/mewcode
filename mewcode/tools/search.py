"""搜索工具：按模式找文件（glob）、搜代码内容（正则）。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from mewcode.tools.base import Tool, ToolResult

# 结果条数上限，超出截断注明
FIND_MAX_ENTRIES = 500
GREP_MAX_LINES = 200

# 内容搜索时跳过的隐藏目录
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}

# 按常见二进制/大文件扩展名跳过（内容搜索只处理文本）
_SKIP_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".pyc", ".so", ".dll", ".exe", ".bin", ".woff", ".woff2", ".ttf", ".eot",
}


class FindFilesTool(Tool):
    """按 glob 模式查找文件。"""

    name: ClassVar[str] = "find_files"
    description: ClassVar[str] = (
        "按 glob 模式查找文件（如 **/*.py、src/*.ts）。"
        "path 为搜索根目录，缺省为当前目录；返回匹配文件的相对路径列表。"
        "找文件请用本工具，不要用 shell 的 find。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "glob 模式，如 **/*.py"},
            "path": {"type": "string", "description": "搜索根目录（可选，缺省当前目录）"},
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict) -> ToolResult:
        root = Path(args.get("path") or ".")
        pattern = args["pattern"]
        try:
            matches = sorted(root.glob(pattern))
        except (OSError, ValueError) as e:
            return ToolResult.failure(f"glob 模式无效或路径错误: {e}")
        files = [m for m in matches if m.is_file()]
        if not files:
            return ToolResult.success(f"未找到匹配 {pattern!r} 的文件")
        lines = []
        for f in files:
            rel = f.relative_to(root) if f.is_absolute() or root != Path(".") else f
            lines.append(str(rel))
        truncated = ""
        if len(lines) > FIND_MAX_ENTRIES:
            lines = lines[:FIND_MAX_ENTRIES]
            truncated = f"\n…（已截断，共 {len(files)} 个匹配，只显示前 {FIND_MAX_ENTRIES} 个）"
        return ToolResult.success("\n".join(lines) + truncated)


class GrepCodeTool(Tool):
    """按正则表达式搜索代码内容。"""

    name: ClassVar[str] = "grep_code"
    description: ClassVar[str] = (
        "按正则表达式搜索目录下文本文件的内容，返回 文件:行号: 内容 列表。"
        "path 为搜索根目录，缺省为当前目录；case_sensitive 缺省为 true。"
        "跳过二进制文件与 .git/.venv 等目录。"
        "搜代码请用本工具，不要用 shell 的 grep。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式"},
            "path": {"type": "string", "description": "搜索根目录（可选，缺省当前目录）"},
            "case_sensitive": {"type": "boolean", "description": "是否区分大小写（可选，缺省 true）"},
        },
        "required": ["pattern"],
    }

    def execute(self, args: dict) -> ToolResult:
        try:
            flags = 0 if args.get("case_sensitive", True) else re.IGNORECASE
            regex = re.compile(args["pattern"], flags)
        except re.error as e:
            return ToolResult.failure(f"正则表达式无效 {args['pattern']!r}: {e}")
        root = Path(args.get("path") or ".")
        if not root.is_dir():
            return ToolResult.failure(f"目录不存在: {root}")

        lines: list[str] = []
        total = 0

        def _search_dir(directory: Path) -> None:
            nonlocal total
            try:
                entries = sorted(directory.iterdir())
            except OSError:
                return
            for entry in entries:
                if entry.name in _SKIP_DIRS:
                    continue
                if entry.is_dir():
                    _search_dir(entry)
                    continue
                if not entry.is_file() or entry.suffix in _SKIP_SUFFIXES:
                    continue
                try:
                    text = entry.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue  # 二进制或不可读文件静默跳过
                for i, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        total += 1
                        if len(lines) < GREP_MAX_LINES:
                            rel = entry.relative_to(root) if root != Path(".") else entry
                            lines.append(f"{rel}:{i}: {line}")

        _search_dir(root)
        if total == 0:
            return ToolResult.success(f"未找到匹配 {args['pattern']!r} 的内容")
        truncated = ""
        if total > GREP_MAX_LINES:
            truncated = f"\n…（已截断，共 {total} 行匹配，只显示前 {GREP_MAX_LINES} 行）"
        return ToolResult.success("\n".join(lines) + truncated)
