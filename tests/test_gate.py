"""闸门测试：层序短路、模式兜底、grant 两式。"""

import pytest

from mewcode.permissions import Action, Mode, PermissionGate, Rule, RuleSet
from mewcode.provider.base import ToolCall
from mewcode.tools import create_registry


@pytest.fixture
def root(tmp_path):
    return tmp_path.resolve()


@pytest.fixture
def registry():
    return create_registry()


def _call(name: str, args: dict) -> ToolCall:
    return ToolCall(id="t1", name=name, args=args)


def _gate(root, *rules: Rule, mode: Mode = Mode.DEFAULT) -> PermissionGate:
    rs = RuleSet()
    for r in rules:
        rs.add(r)
    return PermissionGate(root, rs, mode)


def test_blacklist_beats_rules_and_mode(root, registry):
    """黑名单第一层：allow 规则 + accept 档也拦。"""
    gate = _gate(root, Rule("run_command", "*", Action.ALLOW), mode=Mode.ACCEPT)
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "sudo ls"}))
    assert v.action is Action.DENY
    assert "黑名单" in v.reason


def test_sandbox_beats_rules_and_mode(root, registry):
    gate = _gate(root, Rule("write_file", "*", Action.ALLOW), mode=Mode.ACCEPT)
    v = gate.check(
        registry.get("write_file"),
        _call("write_file", {"file_path": "../escape.txt", "content": "x"}),
    )
    assert v.action is Action.DENY
    assert "越界" in v.reason


def test_sandbox_allows_inside(root, registry):
    gate = _gate(root)
    v = gate.check(
        registry.get("write_file"),
        _call("write_file", {"file_path": "ok.txt", "content": "x"}),
    )
    assert v.action is Action.ASK  # 通过沙箱后落默认询问（副作用工具）


def test_sandbox_find_files_optional_path(root, registry):
    """find_files/grep_code 不带 path 时免检沙箱。"""
    gate = _gate(root)
    v = gate.check(registry.get("find_files"), _call("find_files", {"pattern": "*.py"}))
    assert v.action is Action.ALLOW  # 只读工具


def test_sandbox_find_files_outside_path(root, registry):
    gate = _gate(root, mode=Mode.ACCEPT)
    v = gate.check(
        registry.get("grep_code"),
        _call("grep_code", {"pattern": "x", "path": "/etc"}),
    )
    assert v.action is Action.DENY


def test_deny_rule_wins_in_accept_mode(root, registry):
    gate = _gate(root, Rule("run_command", "git *", Action.DENY), mode=Mode.ACCEPT)
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git push"}))
    assert v.action is Action.DENY


def test_ask_rule_still_asks_in_accept_mode(root, registry):
    """规则优先于模式：accept 档下 ask 规则仍询问。"""
    gate = _gate(root, Rule("run_command", "git *", Action.ASK), mode=Mode.ACCEPT)
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git push"}))
    assert v.action is Action.ASK
    assert "询问规则" in v.reason


def test_allow_rule_allows_in_strict_mode(root, registry):
    gate = _gate(root, Rule("run_command", "git *", Action.ALLOW), mode=Mode.STRICT)
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"}))
    assert v.action is Action.ALLOW


def test_default_mode_unmatched(root, registry):
    gate = _gate(root)
    assert gate.check(registry.get("read_file"), _call("read_file", {"file_path": "a.py"})).action is Action.ALLOW
    assert gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"})).action is Action.ASK
    assert gate.check(registry.get("write_file"), _call("write_file", {"file_path": "a.py", "content": "x"})).action is Action.ASK


def test_strict_mode_unmatched(root, registry):
    gate = _gate(root, mode=Mode.STRICT)
    assert gate.check(registry.get("read_file"), _call("read_file", {"file_path": "a.py"})).action is Action.ALLOW
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"}))
    assert v.action is Action.DENY
    assert "严格模式" in v.reason


def test_accept_mode_unmatched(root, registry):
    gate = _gate(root, mode=Mode.ACCEPT)
    assert gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"})).action is Action.ALLOW
    assert gate.check(registry.get("write_file"), _call("write_file", {"file_path": "a.py", "content": "x"})).action is Action.ALLOW


def test_rule_matches_primary_arg(root, registry):
    """规则匹配主参数：read_file → file_path；grep_code → pattern。"""
    gate = _gate(root, Rule("read_file", "**/*.env", Action.DENY))
    v = gate.check(registry.get("read_file"), _call("read_file", {"file_path": "a/.env"}))
    assert v.action is Action.DENY
    gate2 = _gate(root, Rule("grep_code", "*password*", Action.DENY))
    v2 = gate2.check(registry.get("grep_code"), _call("grep_code", {"pattern": "password="}))
    assert v2.action is Action.DENY


def test_grant_session_takes_effect(root, registry):
    gate = _gate(root, mode=Mode.STRICT)
    gate.grant_session(Rule("run_command", "git status", Action.ALLOW))
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"}))
    assert v.action is Action.ALLOW
    # 未登记的命令仍拒绝（严格档）
    v2 = gate.check(registry.get("run_command"), _call("run_command", {"command": "git diff"}))
    assert v2.action is Action.DENY


def test_grant_permanent_writes_local_and_takes_effect(root, registry, tmp_path):
    local = tmp_path / "permissions.local.yaml"
    rs = RuleSet()
    rs.load_from([], local)
    gate = PermissionGate(root, rs, Mode.STRICT)
    gate.grant_permanent(Rule("run_command", "git status", Action.ALLOW))
    # 立即生效
    v = gate.check(registry.get("run_command"), _call("run_command", {"command": "git status"}))
    assert v.action is Action.ALLOW
    # 重读文件规则仍在
    rs2 = RuleSet()
    rs2.load_from([local], local)
    assert rs2.match("run_command", "git status") == [Action.ALLOW]


def test_cycle_mode(root, registry):
    gate = _gate(root)
    assert gate.cycle_mode() is Mode.ACCEPT
    assert gate.cycle_mode() is Mode.STRICT
    assert gate.cycle_mode() is Mode.DEFAULT


def test_mode_labels(root):
    from mewcode.permissions import MODE_LABELS

    assert MODE_LABELS[Mode.STRICT] == "严格"
    assert MODE_LABELS[Mode.DEFAULT] == "默认"
    assert MODE_LABELS[Mode.ACCEPT] == "放行"
