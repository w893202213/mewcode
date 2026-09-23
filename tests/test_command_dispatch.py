"""分发器单测（ch12 F3/F5）：未命中提示、handler 调用与结果透传。"""

from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.registry import CommandRegistry, CommandSpec
from mewcode.commands.ui import UiController


class FakeUi:
    """记录 display 调用的最小替身。"""

    def __init__(self) -> None:
        self.displayed: list[str] = []

    def display(self, text: str) -> None:
        self.displayed.append(text)


def test_unknown_command_guides_to_help():
    r = CommandRegistry()
    ui = FakeUi()
    result = dispatch(r, ParsedCommand(name="nope", args=""), ui)
    assert result is CommandResult.CONTINUE
    assert ui.displayed and "未知命令" in ui.displayed[0] and "/help" in ui.displayed[0]


def test_empty_name_reports_unknown():
    r = CommandRegistry()
    ui = FakeUi()
    dispatch(r, ParsedCommand(name="", args=""), ui)
    assert ui.displayed and "未知命令" in ui.displayed[0]


def test_handler_receives_parsed_and_ui():
    r = CommandRegistry()
    seen: list = []

    def handler(cmd, ui):
        seen.append((cmd, ui))
        return None

    r.register(CommandSpec(name="test", description="", handler=handler))
    parsed = ParsedCommand(name="test", args="abc")
    ui = FakeUi()
    result = dispatch(r, parsed, ui)
    assert result is CommandResult.CONTINUE  # None 按 CONTINUE
    assert seen == [(parsed, ui)]


def test_exit_result_passed_through():
    r = CommandRegistry()
    r.register(
        CommandSpec(name="exit", description="", handler=lambda cmd, ui: CommandResult.EXIT)
    )
    assert dispatch(r, ParsedCommand(name="exit", args=""), FakeUi()) is CommandResult.EXIT


def test_spec_without_handler_is_unknown():
    r = CommandRegistry()
    r.register(CommandSpec(name="ghost", description=""))
    ui = FakeUi()
    result = dispatch(r, ParsedCommand(name="ghost", args=""), ui)
    assert result is CommandResult.CONTINUE
    assert ui.displayed and "未知命令" in ui.displayed[0]
