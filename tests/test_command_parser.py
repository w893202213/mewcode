"""斜杠解析器单测（ch12 F2）。"""

from mewcode.commands.parser import ParsedCommand, parse_command


def test_blank_input_returns_none():
    assert parse_command("") is None
    assert parse_command("   ") is None
    assert parse_command("\t") is None


def test_plain_text_returns_none():
    assert parse_command("你好") is None
    assert parse_command("帮我把这个函数重构一下") is None


def test_bare_command():
    assert parse_command("/help") == ParsedCommand(name="help", args="")


def test_command_with_args():
    assert parse_command("/help plan") == ParsedCommand(name="help", args="plan")


def test_case_insensitive():
    assert parse_command("/HELP") == ParsedCommand(name="help", args="")
    assert parse_command("/Clear all") == ParsedCommand(name="clear", args="all")


def test_extra_spaces_preserved_in_args():
    # 去掉首个分隔空白，其余多余空格原样保留
    assert parse_command("/help  abc") == ParsedCommand(name="help", args=" abc")


def test_trailing_space_gives_empty_args():
    assert parse_command("/plan ") == ParsedCommand(name="plan", args="")


def test_empty_name():
    assert parse_command("/") == ParsedCommand(name="", args="")
    assert parse_command("/ x") == ParsedCommand(name="", args="x")
