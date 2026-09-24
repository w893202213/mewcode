"""统一 Agent 工具（ch16 F2）：主 Agent 委派子任务的唯一入口。

type 参数分流两条路径：defined（按预定义角色从空白对话执行）与
fork（继承父对话历史与工具集，强制后台）；任务经 ParentContext 里的
TaskManager 启动——同步任务等待完成返回结果，超 60 秒自动转后台。
"""

from __future__ import annotations

from typing import ClassVar

from mewcode.agents.context import current
from mewcode.tools.base import Tool, ToolResult


class AgentTool(Tool):
    """把子任务委派给独立的子 Agent 执行。"""

    name: ClassVar[str] = "agent"
    description: ClassVar[str] = (
        "把子任务委派给独立的子 Agent。type=defined 按预定义角色从空白"
        "对话执行（role 指定角色名）；type=fork 继承当前对话历史与工具集"
        "继续深入（强制后台运行）。background=true 时不等待、立即后台执行"
        "（默认同步等待子 Agent 完成，超 60 秒自动转后台）。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": ["defined", "fork"],
                "description": "委派类型：defined=按角色执行；fork=继承当前对话继续",
            },
            "role": {
                "type": "string",
                "description": "角色名（type=defined 必填；fork 忽略）",
            },
            "prompt": {"type": "string", "description": "子 Agent 的任务描述（必填）"},
            "background": {
                "type": "boolean",
                "description": "true=立即后台执行不等待（fork 恒后台）",
            },
        },
        "required": ["type", "prompt"],
    }
    # 执行超时上限：略大于 60 秒同步上限，兜底 run_tool 的线程 join
    timeout: ClassVar[float] = 70.0
    read_only: ClassVar[bool] = False  # 有副作用（启动子 Agent）

    def execute(self, args: dict) -> ToolResult:
        kind = args.get("type")
        if kind not in ("defined", "fork"):
            return ToolResult.failure(f"type 无效: {kind!r}（可选: defined/fork）")
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult.failure("prompt 必须是非空的任务描述")
        role = args.get("role")
        if kind == "defined" and (not isinstance(role, str) or not role.strip()):
            return ToolResult.failure("type=defined 需要 role 参数（角色名）")
        background = bool(args.get("background", False)) or kind == "fork"
        ctx = current()
        if ctx is None or ctx.task_manager is None:
            return ToolResult.failure("agent 工具仅主对话可用")
        task, err = ctx.task_manager.start(
            kind, role, prompt, background, ctx.agent
        )
        if task is None:
            return ToolResult.failure(err)
        if task.background:
            label = "fork 子 Agent" if kind == "fork" else f"子 Agent（{role}）"
            return ToolResult.success(
                f"{label}已在后台启动，任务 #{task.id}，完成后会通知主对话"
            )
        result = ctx.task_manager.wait_sync(task)
        return ToolResult.success(result)
