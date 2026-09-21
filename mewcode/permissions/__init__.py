"""权限系统：五层防御——黑名单、路径沙箱、可配置规则、权限模式、人在回路。

对 Agent Loop 暴露的核心是 PermissionGate（纯计算裁决）；ask 的暂停
与应答在 agent 层完成（PermissionAskEvent + Agent.answer_permission）。
"""

from mewcode.permissions.gate import MODE_LABELS, Mode, PermissionGate, Verdict
from mewcode.permissions.rules import Action, Rule, RuleSet

__all__ = [
    "Action",
    "MODE_LABELS",
    "Mode",
    "PermissionGate",
    "Rule",
    "RuleSet",
    "Verdict",
]
