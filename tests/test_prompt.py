"""prompt 包测试：模块顺序、三行原文语义、补充块结构、注入节奏、拼装。"""

import datetime

from mewcode.prompt import (
    PLAN_MODE_DIRECTIVE,
    Environment,
    SwitchDirective,
    SystemPromptBuilder,
    build_supplement,
)
from mewcode.prompt.modules import MODULES, STABLE_TEXT


def test_modules_in_priority_order():
    """七个固定模块按优先级排列（F1）。"""
    assert [m.title for m in MODULES] == [
        "身份",
        "系统约束",
        "任务模式",
        "动作执行",
        "工具使用",
        "语气风格",
        "文本输出",
    ]


def test_stable_text_renders_titles_in_order():
    """拼接文本中七个标题按优先级顺序出现，模块间空行分隔。"""
    previous = -1
    for title in [m.title for m in MODULES]:
        pos = STABLE_TEXT.index(f"## {title}")
        assert pos > previous
        previous = pos
    # 模块间以空行分隔（标题之间至少一个空行）
    assert "\n\n## " in STABLE_TEXT


def test_original_three_lines_semantics_preserved():
    """三行原文语义完整吸收进各模块（F1）。"""
    text = STABLE_TEXT
    # 第 1 行：身份
    assert "你是 MewCode" in text and "终端环境" in text and "AI 编程助手" in text
    # 第 2 行：擅长
    assert "阅读代码" in text and "编写代码" in text and "调试" in text
    # 第 3 行：先思考再行动（与 extended thinking 兼容的表述）
    assert "先思考再行动" in text
    assert "推理在思考过程中完成" in text


def test_key_rules_double_reinforced():
    """关键规则同时出现在全局指令与工具描述（F5）。"""
    from mewcode.tools import create_registry

    text = STABLE_TEXT
    # 全局指令侧
    assert "优先使用专用工具" in text
    assert "编辑前必先读" in text
    # 工具描述侧（与全局指令呼应）
    descriptions = {t.name: t.description for t in create_registry().list()}
    assert "不要用 shell 的 cat/sed" in descriptions["read_file"]
    assert "修改文件前必须先用 read_file" in descriptions["edit_file"]
    assert "不要用 shell 的 find" in descriptions["find_files"]
    assert "不要用 shell 的 grep" in descriptions["grep_code"]
    assert "read_file" in descriptions["run_command"]  # 专用工具清单引用


def test_build_supplement_structure():
    """补充块：<supplement> 标签 + 环境信息三行 + 会话状态指令（F3）。"""
    env = Environment(cwd="/home/user/project", platform="Linux 6.6")
    text = build_supplement(env, ["Plan Mode 已开启"], date=datetime.date(2026, 9, 21))
    assert text.startswith("<supplement>")
    assert text.endswith("</supplement>")
    assert "【环境信息】" in text
    assert "- 工作目录: /home/user/project" in text
    assert "- 操作系统: Linux 6.6" in text
    assert "- 当前日期: 2026-09-21" in text
    assert "【会话状态】" in text
    assert "Plan Mode 已开启" in text


def test_build_supplement_without_directives():
    """无指令时省略【会话状态】小节。"""
    env = Environment(cwd="/tmp", platform="Linux")
    text = build_supplement(env, [], date=datetime.date(2026, 1, 1))
    assert "【会话状态】" not in text
    assert "【环境信息】" in text


def test_switch_directive_cadence():
    """注入节奏：首轮完整、周期轮完整、其余精简（F4）。"""
    d = SwitchDirective(name="x", full_text="完整", slim_text="精简", repeat_every=4)
    assert [d.text_for(i) for i in range(1, 9)] == [
        "完整", "精简", "精简", "完整", "精简", "精简", "精简", "完整",
    ]


def test_switch_directive_custom_interval():
    """repeat_every 可自定义。"""
    d = SwitchDirective(name="x", full_text="F", slim_text="S", repeat_every=2)
    assert [d.text_for(i) for i in range(1, 5)] == ["F", "F", "S", "F"]


def test_builder_stable_identical_dynamic_varies():
    """两次 build：stable 逐字节一致；dynamic 随指令与轮次变化（F2）。"""
    builder = SystemPromptBuilder(Environment(cwd="/tmp", platform="Linux"))
    first = builder.build([PLAN_MODE_DIRECTIVE], iteration=1)
    second = builder.build([PLAN_MODE_DIRECTIVE], iteration=2)
    assert first.stable == second.stable == STABLE_TEXT
    assert PLAN_MODE_DIRECTIVE.full_text in first.dynamic
    assert PLAN_MODE_DIRECTIVE.slim_text in second.dynamic
    assert first.dynamic != second.dynamic


def test_builder_environment_in_every_dynamic():
    """环境信息每次 build 都带（含现取日期）。"""
    builder = SystemPromptBuilder(Environment(cwd="/app", platform="Linux 1.0"))
    system = builder.build([], iteration=1)
    assert "工作目录: /app" in system.dynamic
    assert "操作系统: Linux 1.0" in system.dynamic
    assert f"当前日期: {datetime.date.today().isoformat()}" in system.dynamic


def test_builder_custom_instructions_present():
    """配置了自定义指令：dynamic 尾部出现【自定义指令】块（F7）。"""
    builder = SystemPromptBuilder(
        Environment(cwd="/tmp", platform="Linux"), custom_instructions="回答用中文\n代码风格简洁"
    )
    system = builder.build([], iteration=1)
    assert "【自定义指令】" in system.dynamic
    assert "回答用中文\n代码风格简洁" in system.dynamic
    # 位置：自定义指令块在补充块之后
    assert system.dynamic.index("</supplement>") < system.dynamic.index("【自定义指令】")


def test_builder_without_custom_instructions():
    """未配置自定义指令：模块不参与拼装（F7）。"""
    builder = SystemPromptBuilder(Environment(cwd="/tmp", platform="Linux"))
    system = builder.build([], iteration=1)
    assert "【自定义指令】" not in system.dynamic
    assert system.dynamic.endswith("</supplement>")


def test_plan_mode_directive_defaults():
    """Plan Mode 指令常量：节奏默认每 4 轮，完整/精简文本均非空。"""
    assert PLAN_MODE_DIRECTIVE.repeat_every == 4
    assert PLAN_MODE_DIRECTIVE.full_text
    assert PLAN_MODE_DIRECTIVE.slim_text
    assert "只读" in PLAN_MODE_DIRECTIVE.full_text
