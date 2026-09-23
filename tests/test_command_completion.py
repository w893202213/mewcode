"""Tab 补全单测（ch12 F6）：单匹配补主名、多匹配菜单、别名命中补主名。"""

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from mewcode.commands.builtins import build_registry
from mewcode.commands.registry import CommandRegistry, CommandSpec
from mewcode.tui.completion import CommandCompleter


def _complete(completer: CommandCompleter, text: str) -> list[str]:
    doc = Document(text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


def _registry() -> CommandRegistry:
    return build_registry()


def test_single_match_completes_directly():
    assert _complete(CommandCompleter(_registry()), "/cl") == ["/clear"]


def test_multiple_matches_open_menu():
    completions = _complete(CommandCompleter(_registry()), "/c")
    assert completions == ["/clear", "/compact"]


def test_alias_match_completes_main_name():
    assert _complete(CommandCompleter(_registry()), "/mo") == ["/permission"]


def test_alias_and_name_collision_opens_menu():
    completions = _complete(CommandCompleter(_registry()), "/m")
    assert completions == ["/memory", "/permission"]


def test_single_letter_alias():
    assert _complete(CommandCompleter(_registry()), "/q") == ["/exit"]
    assert _complete(CommandCompleter(_registry()), "/h") == ["/help"]


def test_exact_name_single_match():
    assert _complete(CommandCompleter(_registry()), "/help") == ["/help"]


def test_empty_prefix_lists_all():
    assert _complete(CommandCompleter(_registry()), "/") == [
        "/clear",
        "/compact",
        "/do",
        "/exit",
        "/help",
        "/memory",
        "/permission",
        "/plan",
        "/resume",
        "/review",
        "/session",
        "/status",
    ]


def test_no_match_returns_nothing():
    assert _complete(CommandCompleter(_registry()), "/zzz") == []


def test_no_slash_returns_nothing():
    assert _complete(CommandCompleter(_registry()), "hel") == []
    assert _complete(CommandCompleter(_registry()), "") == []


def test_arg_position_not_completed():
    assert _complete(CommandCompleter(_registry()), "/help ") == []
    assert _complete(CommandCompleter(_registry()), "/help pl") == []


def test_hidden_command_not_completed():
    r = CommandRegistry()
    r.register(CommandSpec(name="secret", description="隐藏命令", hidden=True))
    r.register(CommandSpec(name="show", description="可见命令"))
    assert _complete(CommandCompleter(r), "/s") == ["/show"]
