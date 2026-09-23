"""命令注册中心单测（ch12 F1）：登记、别名解析、冲突校验、可见列表。"""

import pytest

from mewcode.commands.registry import (
    CommandConflictError,
    CommandRegistry,
    CommandSpec,
    CommandType,
)


def _spec(name: str, **kw) -> CommandSpec:
    return CommandSpec(name=name, description=kw.pop("description", f"{name} 描述"), **kw)


def test_register_and_resolve_by_name():
    r = CommandRegistry()
    r.register(_spec("help", handler=lambda cmd, ui: None))
    assert r.resolve("help").name == "help"


def test_resolve_by_alias():
    r = CommandRegistry()
    r.register(_spec("exit", aliases=("q", "quit")))
    assert r.resolve("q").name == "exit"
    assert r.resolve("quit").name == "exit"


def test_resolve_case_insensitive():
    r = CommandRegistry()
    r.register(_spec("help"))
    assert r.resolve("HELP").name == "help"


def test_resolve_unknown_returns_none():
    r = CommandRegistry()
    r.register(_spec("help"))
    assert r.resolve("nope") is None


def test_register_normalizes_case():
    r = CommandRegistry()
    r.register(_spec("Help", aliases=("H",)))
    assert r.resolve("help").name == "help"
    assert r.resolve("h").name == "help"


def test_duplicate_name_conflict():
    r = CommandRegistry()
    r.register(_spec("help"))
    r.register(_spec("help", description="另一条"))
    problems = r.validate()
    assert any("重复注册" in p for p in problems)


def test_alias_collides_with_other_name():
    r = CommandRegistry()
    r.register(_spec("permission", aliases=("mode",)))
    r.register(_spec("mode"))
    problems = r.validate()
    assert any("撞命令" in p for p in problems)


def test_alias_collides_with_other_alias():
    r = CommandRegistry()
    r.register(_spec("help", aliases=("h",)))
    r.register(_spec("history", aliases=("h",)))
    problems = r.validate()
    assert any("同时是" in p for p in problems)


def test_alias_equals_own_name():
    r = CommandRegistry()
    r.register(_spec("help", aliases=("help",)))
    problems = r.validate()
    assert any("撞命令" in p for p in problems)


def test_illegal_name():
    r = CommandRegistry()
    r.register(_spec(""))
    r.register(_spec("bad name"))
    problems = r.validate()
    assert len(problems) == 2


def test_illegal_alias():
    r = CommandRegistry()
    r.register(_spec("help", aliases=("",)))
    problems = r.validate()
    assert any("别名非法" in p for p in problems)


def test_validate_empty_registry_passes():
    assert CommandRegistry().validate() == []


def test_visible_excludes_hidden_and_sorts():
    r = CommandRegistry()
    r.register(_spec("zeta"))
    r.register(_spec("alpha"))
    r.register(_spec("secret", hidden=True))
    names = [s.name for s in r.visible()]
    assert names == ["alpha", "zeta"]


def test_all_includes_hidden():
    r = CommandRegistry()
    r.register(_spec("alpha"))
    r.register(_spec("secret", hidden=True))
    assert len(r.all()) == 2


def test_conflict_error_carries_details():
    r = CommandRegistry()
    r.register(_spec("help"))
    r.register(_spec("help", description="另一条"))
    problems = r.validate()
    err = CommandConflictError(problems)
    assert "重复注册" in str(err)
