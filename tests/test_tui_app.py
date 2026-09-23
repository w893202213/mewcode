"""TUI 状态栏单测（ch12 F4）：返回 prompt_toolkit 可接受的纯文本。

回归锚点：bottom_toolbar 返回 rich Text 会抛「No formatted text」异常
（tmux 真机冒烟发现），必须返回 str。
"""

from pathlib import Path

from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.tui.app import _status_toolbar
from mewcode.tui.double_press import DoublePressTracker


class _Agent:
    def __init__(self, plan_mode: bool = False) -> None:
        self.plan_mode = plan_mode


def _gate(mode: Mode) -> PermissionGate:
    return PermissionGate(Path.cwd().resolve(), RuleSet(), mode)


def test_default_mode():
    text = _status_toolbar(DoublePressTracker(), _gate(Mode.DEFAULT), _Agent())
    assert text == "[DEFAULT]"
    assert isinstance(text, str)


def test_strict_mode():
    text = _status_toolbar(DoublePressTracker(), _gate(Mode.STRICT), _Agent())
    assert text == "[STRICT]"


def test_accept_mode_with_plan():
    text = _status_toolbar(DoublePressTracker(), _gate(Mode.ACCEPT), _Agent(plan_mode=True))
    assert text == "[ACCEPT] [PLAN]"


def test_default_mode_with_plan():
    text = _status_toolbar(DoublePressTracker(), _gate(Mode.DEFAULT), _Agent(plan_mode=True))
    assert text == "[DEFAULT] [PLAN]"


def test_hint_active_replaces_status():
    tracker = DoublePressTracker()
    tracker.press()
    text = _status_toolbar(tracker, _gate(Mode.DEFAULT), _Agent())
    assert text == "再按一次 Ctrl+C 退出"
