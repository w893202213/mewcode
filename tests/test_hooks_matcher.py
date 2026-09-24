"""Hook 条件引擎测试（ch15 F3）：四模式匹配、点路径、all/any 组合。"""

from mewcode.hooks.loader import HookCondition
from mewcode.hooks.matcher import MISSING, eval_conditions, get_path, match_pattern


# ----------------------------------------------------------------------
# 四种模式匹配
# ----------------------------------------------------------------------


def test_exact_match():
    assert match_pattern("git status", "git status") is True
    assert match_pattern("git status", "git status -s") is False


def test_glob_match():
    assert match_pattern("*.md", "a.md") is True
    assert match_pattern("*.md", "a.txt") is False
    # `**/` 归一为 `*`，跨路径分隔符匹配（与权限规则口径一致）
    assert match_pattern("**/x", "a/b/x") is True


def test_negate_match():
    # `!` 前缀：剩余部分匹配后取反
    assert match_pattern("!/tmp/*", "/tmp/a") is False
    assert match_pattern("!/tmp/*", "/home/a") is True


def test_regex_match():
    # fullmatch 语义：正则须覆盖整个字符串
    assert match_pattern("/rm -.*/", "rm -rf /") is True
    assert match_pattern("/rm -.*/", "cp file") is False


def test_negate_strips_one_layer():
    # `!!` 剥一层取反后精确匹配（等价于裸精确）
    assert match_pattern("!!git", "git") is True
    assert match_pattern("!!git", "status") is False


def test_non_string_value_strified():
    assert match_pattern("True", True) is True
    assert match_pattern("1", 1) is True


# ----------------------------------------------------------------------
# 点路径取值
# ----------------------------------------------------------------------


def test_get_path_nested():
    ctx = {"args": {"command": "git status"}}
    assert get_path(ctx, "args.command") == "git status"


def test_get_path_missing():
    ctx = {"args": {}}
    assert get_path(ctx, "args.missing") is MISSING
    assert get_path(ctx, "tool") is MISSING


def test_get_path_midway_not_dict():
    ctx = {"args": "not-a-dict"}
    assert get_path(ctx, "args.command") is MISSING


# ----------------------------------------------------------------------
# all/any 组合
# ----------------------------------------------------------------------


def _cond(field: str, pattern: str) -> HookCondition:
    return HookCondition(field=field, pattern=pattern)


TOOL_CTX = {"tool": "run_command", "args": {"command": "git status"}}


def test_eval_all_both_true():
    conds = [_cond("tool", "run_command"), _cond("args.command", "git *")]
    assert eval_conditions(conds, "all", TOOL_CTX) is True


def test_eval_all_one_false():
    conds = [_cond("tool", "run_command"), _cond("args.command", "rm *")]
    assert eval_conditions(conds, "all", TOOL_CTX) is False


def test_eval_any_one_true():
    conds = [_cond("tool", "run_command"), _cond("args.command", "rm *")]
    assert eval_conditions(conds, "any", TOOL_CTX) is True


def test_eval_any_both_false():
    conds = [_cond("tool", "read_file"), _cond("args.command", "rm *")]
    assert eval_conditions(conds, "any", TOOL_CTX) is False


def test_eval_empty_conditions_always_true():
    assert eval_conditions([], "all", {}) is True
    assert eval_conditions([], "any", {}) is True


def test_eval_missing_field_is_false():
    # 参数缺失 → 条件不命中（spec F3）
    conds = [_cond("args.command", "git *")]
    assert eval_conditions(conds, "all", {"tool": "run_command"}) is False
