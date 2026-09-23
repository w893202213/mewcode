"""技能 TUI 接入测试（ch13 F7）：/clear 联动清技能、run_skill 模式分流。"""

import io
from pathlib import Path

from rich.console import Console

from mewcode.commands.registry import CommandRegistry
from mewcode.config import ProviderConfig
from mewcode.session import ChatSession
from mewcode.skills.manager import SkillManager
from mewcode.skills.model import parse_skill_file
from mewcode.tui.uicontroller import TuiUiController


def _manager(tmp_path, name="demo", extra=""):
    path = tmp_path / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: 说明{extra}\n---\nSOP $ARGS\n",
        encoding="utf-8",
    )
    skill, error = parse_skill_file(path, "builtin")
    assert error is None
    return SkillManager({name: skill})


def _controller(tmp_path, manager, run_independent=None):
    """最小装配的 TuiUiController；console 输出进 StringIO 便于断言。"""
    buf = io.StringIO()
    console = Console(file=buf, width=120)
    ui = TuiUiController(
        console=console,
        session=ChatSession(),
        agent=None,
        gate=None,
        memory=None,
        config=ProviderConfig(
            name="t", protocol="anthropic", model="m", base_url="", api_key="k"
        ),
        root=tmp_path,
        run_agent_round=lambda text: None,
        invalidate=lambda: None,
        skill_manager=manager,
        run_independent=run_independent,
    )
    ui._test_buffer = buf
    return ui


def test_clear_conversation_clears_skills(tmp_path):
    """e2e 发现的回归锚点：/clear 必须顺带清掉已激活技能与技能命令。"""
    manager = _manager(tmp_path)
    registry = CommandRegistry()
    manager.attach_registry(registry)
    manager.activate("demo")
    assert registry.resolve("demo") is not None
    ui = _controller(tmp_path, manager)
    ui.clear_conversation()
    assert registry.resolve("demo") is None  # 技能命令已撤销
    assert manager.active_snapshot() == ""  # 激活状态已清空


def test_run_skill_shared_sends_sop(tmp_path):
    manager = _manager(tmp_path)
    ui = _controller(tmp_path, manager)
    sent = []
    ui._run_agent_round = lambda text: sent.append(text)
    ui.run_skill("demo", "参数内容")
    assert len(sent) == 1
    assert "SOP 参数内容" in sent[0]  # 参数已替换后作为用户消息注入


def test_run_skill_independent_delegates(tmp_path):
    manager = _manager(tmp_path, extra="\nmode: independent")
    calls = []
    ui = _controller(tmp_path, manager, run_independent=lambda n, a: calls.append((n, a)))
    ui.run_skill("demo", "x")
    assert calls == [("demo", "x")]


def test_run_skill_unknown_shows_error(tmp_path):
    manager = _manager(tmp_path)
    ui = _controller(tmp_path, manager)
    ui.run_skill("nope", "")
    assert "未知技能" in ui._test_buffer.getvalue()
