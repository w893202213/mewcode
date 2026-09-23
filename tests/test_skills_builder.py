"""系统提示技能注入测试（ch13 F3/F4/N5）：stable 清单 + dynamic 激活块位置。"""

from mewcode.prompt.builder import Environment, SystemPromptBuilder
from mewcode.prompt.modules import STABLE_TEXT


def _builder(skills_summary="", skills_loader=None):
    return SystemPromptBuilder(
        Environment(cwd="/proj", platform="Linux"),
        custom_instructions="自定义指令内容",
        skills_summary=skills_summary,
        skills_loader=skills_loader,
    )


def test_skills_summary_appended_to_stable():
    builder = _builder(skills_summary="【技能】\n- commit: 提交代码")
    prompt = builder.build([], 1)
    assert prompt.stable == STABLE_TEXT + "\n\n【技能】\n- commit: 提交代码"
    # dynamic 不受影响
    assert "【技能】" not in prompt.dynamic


def test_empty_summary_keeps_stable_unchanged():
    builder = _builder(skills_summary="")
    prompt = builder.build([], 1)
    assert prompt.stable == STABLE_TEXT


def test_active_skills_block_after_supplement():
    calls = []
    builder = _builder(
        skills_loader=lambda: calls.append(1) or "【已激活技能】\n### commit\nSOP 内容"
    )
    prompt = builder.build([], 1)
    dynamic = prompt.dynamic
    # 位置：补充块之后、记忆之前、自定义指令之前（F4 最显眼位置）
    assert dynamic.index("<supplement>") < dynamic.index("【已激活技能】")
    assert dynamic.index("【已激活技能】") < dynamic.index("【自定义指令】")
    assert "SOP 内容" in dynamic


def test_skills_loader_fetched_every_build():
    state = {"n": 0}

    def loader():
        state["n"] += 1
        return f"第 {state['n']} 版"

    builder = _builder(skills_loader=loader)
    assert "第 1 版" in builder.build([], 1).dynamic
    assert "第 2 版" in builder.build([], 2).dynamic  # 每轮重建都在（AC5）


def test_empty_snapshot_omits_block():
    builder = _builder(skills_loader=lambda: "")
    prompt = builder.build([], 1)
    assert "【已激活技能】" not in prompt.dynamic


def test_no_loader_no_block():
    prompt = _builder().build([], 1)
    assert "【已激活技能】" not in prompt.dynamic
    assert "【技能】" not in prompt.dynamic
