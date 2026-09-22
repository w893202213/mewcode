"""规则引擎测试：匹配、合并裁决、三 YAML 读写。"""

import pytest
import yaml

from mewcode.config import ConfigError
from mewcode.permissions.rules import Action, Rule, RuleSet


def _rs(*rules: Rule) -> RuleSet:
    rs = RuleSet()
    for r in rules:
        rs.add(r)
    return rs


# ---------------------------------------------------------------------------
# 匹配与裁决（T1）
# ---------------------------------------------------------------------------


def test_exact_match():
    rs = _rs(Rule("run_command", "git status", Action.ALLOW))
    assert rs.match("run_command", "git status") == [Action.ALLOW]
    assert rs.match("run_command", "git diff") == []


def test_glob_match():
    rs = _rs(Rule("run_command", "git *", Action.ALLOW))
    assert rs.match("run_command", "git status") == [Action.ALLOW]
    assert rs.match("run_command", "git log --oneline") == [Action.ALLOW]
    assert rs.match("run_command", "echo hi") == []


def test_glob_star_crosses_separators():
    """`**` 归一为 `*`：`*` 跨路径分隔符匹配（技术决策）。"""
    rs = _rs(Rule("read_file", "**/*.env", Action.DENY))
    assert rs.match("read_file", "a/b/.env") == [Action.DENY]
    assert rs.match("read_file", ".env") == [Action.DENY]


def test_tool_scoped():
    """规则只对声明工具生效。"""
    rs = _rs(Rule("run_command", "git *", Action.ALLOW))
    assert rs.match("read_file", "git log") == []


def test_verdict_deny_over_ask_over_allow():
    """多条命中：deny > ask > allow，与注册顺序、层级无关。"""
    rs = _rs(
        Rule("run_command", "git *", Action.ALLOW),
        Rule("run_command", "git *", Action.ASK),
        Rule("run_command", "git *", Action.DENY),
    )
    assert rs.verdict("run_command", "git push", Action.ASK) is Action.DENY
    rs2 = _rs(
        Rule("run_command", "git *", Action.ASK),
        Rule("run_command", "git *", Action.ALLOW),
    )
    assert rs2.verdict("run_command", "git push", Action.ALLOW) is Action.ASK


def test_verdict_no_match_returns_default():
    rs = _rs(Rule("run_command", "git *", Action.ALLOW))
    assert rs.verdict("run_command", "echo hi", Action.ASK) is Action.ASK
    assert rs.verdict("write_file", "foo.py", Action.DENY) is Action.DENY


# ---------------------------------------------------------------------------
# 三 YAML 读写合并（T2）
# ---------------------------------------------------------------------------


def _write_rules(path, *rules: Rule) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rules": [
            {"tool": r.tool, "pattern": r.pattern, "action": r.action.value}
            for r in rules
        ]
    }
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_load_from_merges_files(tmp_path):
    user = tmp_path / "user.yaml"
    project = tmp_path / "project.yaml"
    local = tmp_path / "local.yaml"
    _write_rules(user, Rule("run_command", "git *", Action.ALLOW))
    _write_rules(project, Rule("read_file", "**/*.env", Action.DENY))
    _write_rules(local, Rule("run_command", "git *", Action.ASK))

    rs = RuleSet()
    rs.load_from([user, project, local], local)
    # 合并后三条规则全在：run_command 的 allow+ask → ask；read_file 的 deny → deny
    assert rs.verdict("run_command", "git push", Action.ALLOW) is Action.ASK
    assert rs.verdict("read_file", "a/.env", Action.ALLOW) is Action.DENY


def test_load_from_missing_file_skipped(tmp_path):
    rs = RuleSet()
    rs.load_from([tmp_path / "not_exist.yaml"], tmp_path / "local.yaml")
    assert rs.rules == []


def test_load_from_broken_yaml_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [unclosed", encoding="utf-8")
    rs = RuleSet()
    with pytest.raises(ConfigError, match="bad.yaml"):
        rs.load_from([bad], tmp_path / "local.yaml")


def test_load_from_invalid_structure_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not_a_dict", encoding="utf-8")
    rs = RuleSet()
    with pytest.raises(ConfigError, match="rules 列表"):
        rs.load_from([bad], tmp_path / "local.yaml")


def test_load_from_any_tool_name_ok(tmp_path):
    """ch08 F8：任意工具名（含 MCP 工具名）都可加载，存在性交给 validate_tools。"""
    bad = tmp_path / "bad.yaml"
    _write_rules(bad, Rule("mcp_search", "*", Action.ALLOW))
    rs = RuleSet()
    rs.load_from([bad], tmp_path / "local.yaml")
    assert rs.match("mcp_search", "") == [Action.ALLOW]


def test_load_from_empty_tool_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n  - tool: ''\n    pattern: x\n    action: allow\n", encoding="utf-8"
    )
    rs = RuleSet()
    with pytest.raises(ConfigError, match="tool 无效"):
        rs.load_from([bad], tmp_path / "local.yaml")


def test_validate_tools_known_and_unknown(tmp_path):
    """注册完成后统一校验：合法名通过，非法名报 ConfigError 并列出。"""
    good = tmp_path / "good.yaml"
    _write_rules(good, Rule("mcp_search", "*", Action.ALLOW))
    rs = RuleSet()
    rs.load_from([good], tmp_path / "local.yaml")
    rs.validate_tools({"mcp_search"})  # 合法：不抛

    bad = tmp_path / "bad.yaml"
    _write_rules(bad, Rule("no_such_tool", "*", Action.ALLOW))
    rs2 = RuleSet()
    rs2.load_from([bad], tmp_path / "local.yaml")
    with pytest.raises(ConfigError, match="no_such_tool"):
        rs2.validate_tools({"mcp_search"})


def test_validate_tools_ignores_session_rules(tmp_path):
    """会话规则（add 构造）不参与校验。"""
    rs = RuleSet()
    rs.load_from([], tmp_path / "local.yaml")
    rs.add(Rule("session_only_tool", "", Action.ALLOW))  # 会话规则：真实调用构造
    rs.validate_tools(set())  # 不抛


def test_load_from_invalid_action_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n  - tool: run_command\n    pattern: git *\n    action: maybe\n",
        encoding="utf-8",
    )
    rs = RuleSet()
    with pytest.raises(ConfigError, match="action 无效"):
        rs.load_from([bad], tmp_path / "local.yaml")


def test_append_to_local_file_roundtrip(tmp_path):
    local = tmp_path / "local.yaml"
    rs = RuleSet()
    rs.load_from([], local)
    rs.append_to_local_file(Rule("run_command", "git status", Action.ALLOW))
    rs.append_to_local_file(Rule("run_command", "git status", Action.ALLOW))  # 幂等
    rs2 = RuleSet()
    rs2.load_from([local], local)
    assert rs2.match("run_command", "git status") == [Action.ALLOW]
    assert len(rs2.rules) == 1


def test_append_to_local_file_preserves_existing(tmp_path):
    local = tmp_path / "local.yaml"
    _write_rules(local, Rule("read_file", "**/*.env", Action.DENY))
    rs = RuleSet()
    rs.load_from([], local)
    rs.append_to_local_file(Rule("run_command", "git status", Action.ALLOW))
    rs2 = RuleSet()
    rs2.load_from([local], local)
    assert rs2.verdict("read_file", "a/.env", Action.ALLOW) is Action.DENY
    assert rs2.verdict("run_command", "git status", Action.ALLOW) is Action.ALLOW


# ---------------------------------------------------------------------------
# 空 pattern 处理（ch10）
# ---------------------------------------------------------------------------


def test_load_from_empty_pattern_normalized_with_warning(tmp_path, capsys):
    """ch10 F2：空 pattern 规范化为 * 并打印提示，不阻塞加载。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n  - tool: resolve-library-id\n    pattern: ''\n    action: allow\n",
        encoding="utf-8",
    )
    rs = RuleSet()
    rs.load_from([bad], tmp_path / "local.yaml")
    assert rs.match("resolve-library-id", "*") == [Action.ALLOW]
    assert "第 1 条 pattern 为空" in capsys.readouterr().err


def test_append_normalizes_silently(tmp_path, capsys):
    """ch10：追加写回时旧空 pattern 静默重写为 *，不重复提示。"""
    local = tmp_path / "local.yaml"
    local.write_text(
        "rules:\n  - tool: resolve-library-id\n    pattern: ''\n    action: allow\n",
        encoding="utf-8",
    )
    rs = RuleSet()
    rs.load_from([], local)
    rs.append_to_local_file(Rule("run_command", "git status", Action.ALLOW))
    assert capsys.readouterr().err == ""
    data = yaml.safe_load(local.read_text(encoding="utf-8"))
    assert data["rules"][0]["pattern"] == "*"  # 旧空 pattern 已自愈
    assert data["rules"][1]["pattern"] == "git status"


def test_load_from_none_pattern_raises(tmp_path):
    """ch10 F3：pattern 键缺失（None）仍报错。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n  - tool: run_command\n    pattern:\n    action: allow\n",
        encoding="utf-8",
    )
    rs = RuleSet()
    with pytest.raises(ConfigError, match="pattern 无效"):
        rs.load_from([bad], tmp_path / "local.yaml")


def test_load_from_non_string_pattern_raises(tmp_path):
    """ch10 F3：pattern 非字符串仍报错，信息可定位。"""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n  - tool: run_command\n    pattern: 123\n    action: allow\n",
        encoding="utf-8",
    )
    rs = RuleSet()
    with pytest.raises(ConfigError, match="pattern 无效"):
        rs.load_from([bad], tmp_path / "local.yaml")
