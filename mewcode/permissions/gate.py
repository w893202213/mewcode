"""权限闸门：按固定层序编排黑名单 → 沙箱 → 规则 → 模式兜底，输出裁决。

纯计算、不阻塞：ask 的暂停发生在 Agent 层（事件 + answer_permission）。
任一层拒绝即短路（spec F7）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from mewcode.permissions.blacklist import find_hit
from mewcode.permissions.rules import Action, Rule, RuleSet
from mewcode.permissions.sandbox import check_inside
from mewcode.provider.base import ToolCall
from mewcode.tools.base import Tool

# 五个文件工具：受路径沙箱约束；规则匹配用各自的主参数
_FILE_TOOLS = {"read_file", "write_file", "edit_file", "find_files", "grep_code"}
# 沙箱检查的路径参数名（find_files/grep_code 的 path 可选，缺省 "." 免检）
_SANDBOX_PATH_ARGS = {
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "find_files": "path",
    "grep_code": "path",
}


class Mode(Enum):
    """权限模式三档：只填规则未命中部分，不覆盖已命中规则的裁决。"""

    STRICT = "strict"    # 未命中: 只读放行、副作用拒绝
    DEFAULT = "default"  # 未命中: 只读放行、副作用询问
    ACCEPT = "accept"    # 未命中: 一律放行（deny 规则/黑名单/沙箱仍生效）

    def next(self) -> "Mode":
        """循环切换：strict → default → accept → strict。"""
        return _NEXT_MODE[self]


_NEXT_MODE = {Mode.STRICT: Mode.DEFAULT, Mode.DEFAULT: Mode.ACCEPT, Mode.ACCEPT: Mode.STRICT}

# 档位中文名（TUI 展示）
MODE_LABELS = {Mode.STRICT: "严格", Mode.DEFAULT: "默认", Mode.ACCEPT: "放行"}


@dataclass
class Verdict:
    """一次权限裁决：动作 + 中文原因（拒绝/询问时用于反馈）。"""

    action: Action
    reason: str = ""


def _rule_value(tool: Tool, call: ToolCall) -> str:
    """规则匹配用的参数值：run_command → command；文件工具 → 主参数。

    read_file/write_file/edit_file 用 file_path；find_files/grep_code 的
    主参数是 pattern（path 由沙箱单独约束）。
    """
    if tool.name == "run_command":
        return str(call.args.get("command", ""))
    if tool.name in {"read_file", "write_file", "edit_file"}:
        return str(call.args.get("file_path", ""))
    if tool.name in {"find_files", "grep_code"}:
        return str(call.args.get("pattern", ""))
    return ""


class PermissionGate:
    """集中闸门：黑名单 → 沙箱 → 规则合并 → 模式兜底。"""

    def __init__(self, root: Path, rules: RuleSet, mode: Mode = Mode.DEFAULT) -> None:
        self._root = root.resolve()
        self._rules = rules
        self.mode = mode

    def check(self, tool: Tool, call: ToolCall) -> Verdict:
        """按层序裁决一次调用；任一层拒绝即短路。"""
        # 层 1：黑名单（仅 run_command，硬拦截、不可放开）
        if tool.name == "run_command":
            hit = find_hit(str(call.args.get("command", "")))
            if hit is not None:
                return Verdict(Action.DENY, f"命中黑名单: {hit}，已拒绝执行")
        # 层 2：路径沙箱（五个文件工具，硬边界）
        if tool.name in _FILE_TOOLS:
            path_arg = _SANDBOX_PATH_ARGS[tool.name]
            raw = call.args.get(path_arg)
            if isinstance(raw, str) and raw.strip():
                ok, reason = check_inside(self._root, raw)
                if not ok:
                    return Verdict(Action.DENY, reason)
        # 层 3：规则合并（deny > ask > allow，与所在层级无关）
        value = _rule_value(tool, call)
        actions = self._rules.match(tool.name, value)
        if Action.DENY in actions:
            return Verdict(Action.DENY, f"命中拒绝规则 {tool.name}({value})")
        if Action.ASK in actions:
            return Verdict(Action.ASK, f"命中询问规则 {tool.name}({value})，需要人工确认")
        if Action.ALLOW in actions:
            return Verdict(Action.ALLOW)
        # 层 4：模式兜底（只填未命中）
        if self.mode is Mode.ACCEPT:
            return Verdict(Action.ALLOW)
        if tool.read_only:
            return Verdict(Action.ALLOW)
        if self.mode is Mode.STRICT:
            return Verdict(Action.DENY, "严格模式：未命中放行规则的工具一律拒绝")
        return Verdict(Action.ASK, "默认模式：有副作用的工具需要人工确认")

    def grant_session(self, rule: Rule) -> None:
        """本会话允许：规则进内存集合，退出即弃。"""
        self._rules.add(rule)

    def grant_permanent(self, rule: Rule) -> None:
        """永久允许：规则写入本地级 YAML，同时进内存立即生效。"""
        self._rules.add(rule)
        self._rules.append_to_local_file(rule)

    def cycle_mode(self) -> Mode:
        """循环切换档位并返回新档位。"""
        self.mode = self.mode.next()
        return self.mode
