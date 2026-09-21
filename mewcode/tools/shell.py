"""命令执行工具：在 shell 中运行命令，执行前必须经用户确认。

不设白名单/沙箱（spec 不做的事）；子进程带超时兜底，防止悬挂。
"""

from __future__ import annotations

import subprocess
from typing import ClassVar

from mewcode.tools.base import Tool, ToolResult


class RunCommandTool(Tool):
    """在 shell 中执行命令。"""

    name: ClassVar[str] = "run_command"
    description: ClassVar[str] = (
        "在 shell 中执行一条命令（工作目录为 MewCode 启动目录），"
        "返回标准输出、标准错误与退出码。"
        "仅当专用工具（read_file/write_file/edit_file/find_files/grep_code）"
        "覆盖不到的需求才使用本工具。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
        },
        "required": ["command"],
    }
    requires_confirmation: ClassVar[bool] = True
    read_only: ClassVar[bool] = False  # 执行任意命令：有副作用，plan 模式不可用

    def execute(self, args: dict) -> ToolResult:
        command = args["command"]
        # 子进程超时略小于工具通用超时：先杀进程，避免 run_tool 线程 join 先触发
        subprocess_timeout = max(self.timeout - 2.0, 0.5)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=subprocess_timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult.failure(
                f"命令超时（{subprocess_timeout:g} 秒）已终止: {command}"
            )
        except OSError as e:
            return ToolResult.failure(f"命令执行失败: {e}")

        parts: list[str] = []
        if proc.stdout:
            parts.append(f"[stdout]\n{proc.stdout.rstrip()}")
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
        if not parts:
            parts.append("（无输出）")
        body = "\n".join(parts)
        if proc.returncode == 0:
            return ToolResult.success(body)
        return ToolResult.failure(f"{body}\n[退出码 {proc.returncode}]")
