"""prompt 包：系统提示的唯一生产者。

对外暴露环境信息快照、会话级开关指令、拼装器与 Plan Mode 指令常量。
拼装全程纯函数、可脱离网络与界面单测。
"""

from mewcode.prompt.builder import (
    Environment,
    SwitchDirective,
    SystemPromptBuilder,
    build_supplement,
)

# Plan Mode 指令：完整版（ch05 语义迁移，现走系统通道按轮次注入）、
# 精简版（仅提示只读工具限制）、节奏每 4 轮重复完整版
PLAN_MODE_DIRECTIVE = SwitchDirective(
    name="plan_mode",
    full_text=(
        "Plan Mode 已开启：你只能使用只读工具进行调研，请输出分析与实施计划，"
        "不要修改任何文件或执行命令。用户输入 /do 后才会切回执行模式。"
    ),
    slim_text="（Plan Mode 开启中：仅只读工具可用）",
    repeat_every=4,
)

__all__ = [
    "Environment",
    "SwitchDirective",
    "SystemPromptBuilder",
    "build_supplement",
    "PLAN_MODE_DIRECTIVE",
]
