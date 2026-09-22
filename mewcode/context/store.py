"""工具结果存盘：超阈值结果写入磁盘、会话里留预览与路径（ch09 F3）。

存盘位置 <启动目录>/.mewcode/context/，文件名 tool_result_<seq>.txt
（seq 由调用方递增传入）。存盘失败抛 StoreError，由压缩器降级处理。
"""

from __future__ import annotations

from pathlib import Path


class StoreError(Exception):
    """存盘失败，消息中文、可读。"""


class ResultStore:
    """工具结果文件存取。"""

    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        try:
            base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise StoreError(f"无法创建存盘目录 {base_dir}: {e}") from e

    def save(self, content: str, seq: int) -> Path:
        """完整内容写入 tool_result_<seq>.txt，返回文件路径。"""
        path = self._base_dir / f"tool_result_{seq}.txt"
        try:
            path.write_text(content, encoding="utf-8")
        except OSError as e:
            raise StoreError(f"结果存盘失败 {path}: {e}") from e
        return path

    def preview(self, content: str, path: Path, head_chars: int) -> str:
        """生成会话里保留的替换文本：前 N 字符预览 + 文件路径 + 重读提示。"""
        head = content[:head_chars]
        if len(content) > head_chars:
            head += "\n…"
        return (
            f"{head}\n（完整内容已存盘，共 {len(content)} 字符）\n"
            f"文件路径: {path}\n"
            "需要完整内容时请读取该文件。"
        )
