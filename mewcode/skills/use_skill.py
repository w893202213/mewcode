"""UseSkillTool：系统级技能加载工具（ch13 F3/F5/F6/F8）。

模型经本工具按名加载技能完整指令：
- shared 技能：激活后工具结果直接返回完整 SOP（参数已替换），模型当轮
  即可见；SOP 同时钉入环境上下文，后续每轮系统提示重建时都在
- independent 技能：激活并登记 pending，主循环在本轮结束后执行独立
  对话，摘要回流主历史

系统级属性：read_only=True（计划模式可见）、不受技能白名单过滤约束
（Agent 可见工具计算时始终放行 use_skill）。
"""

from __future__ import annotations

from mewcode.skills.manager import SkillManager
from mewcode.tools.base import Tool, ToolResult

# 系统级工具名（Agent 白名单过滤永远放行）
USE_SKILL_TOOL_NAME = "use_skill"


class UseSkillTool(Tool):
    """加载并激活一个技能，返回该技能的完整操作指令。"""

    name = USE_SKILL_TOOL_NAME
    description = (
        "加载并激活一个技能，返回该技能的完整操作指令（SOP）。"
        "参数 name 为技能名（可用技能见系统提示的【技能】列表），"
        "args 为传给技能的内容（技能正文中的 $ARGS 占位符会被替换）。"
        "激活后技能指令会注入环境上下文，可用工具会按技能白名单收窄。"
        "独立模式（independent）的技能不会立即执行：本轮结束后由系统自动"
        "在独立对话中执行，结果摘要回流本对话。"
    )
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "技能名（系统提示【技能】列表中的名字）"},
            "args": {"type": "string", "description": "传给技能的参数，替换正文中的 $ARGS（可选）"},
        },
        "required": ["name"],
    }
    read_only = True  # 只读标记：计划模式下可见（加载指令本身无副作用）

    def __init__(self, manager: SkillManager) -> None:
        self._manager = manager

    def execute(self, args: dict) -> ToolResult:
        name = args["name"]
        skill_args = args.get("args", "") or ""
        result = self._manager.activate(name, skill_args)
        if not result.ok:
            return ToolResult.failure(result.error)
        skill = result.skill
        warnings = self._manager.take_warnings()
        warning_text = ("\n".join(warnings) + "\n") if warnings else ""
        if skill.mode.value == "independent":
            # 独立模式：登记 pending，本轮结束后由主循环执行（ch13 F8）
            self._manager.set_pending(name, skill_args)
            return ToolResult.success(
                f"技能 {name} 已激活（独立模式），本轮结束后自动在独立对话中执行，"
                f"结果摘要回流本对话。\n{warning_text}"
            )
        return ToolResult.success(
            f"技能 {name} 已激活，以下为完整指令（已注入环境上下文，参数已替换）：\n\n"
            f"{result.body}\n{warning_text}"
        )
