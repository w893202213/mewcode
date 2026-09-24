"""Hook 规则加载与集中校验测试（ch15 F8）。"""

import pytest
import yaml

from mewcode.config import ConfigError
from mewcode.hooks.loader import (
    DEFAULT_TIMEOUT,
    HookAction,
    HookCondition,
    HookRule,
    load_hooks,
    parse_hooks,
)


# ----------------------------------------------------------------------
# 合法样例
# ----------------------------------------------------------------------


def test_parse_four_action_types():
    data = {
        "hooks": [
            {"event": "startup", "action": {"type": "command", "run": "echo hi"}},
            {"event": "round_start", "action": {"type": "inject", "prompt": "注意安全"}},
            {"event": "tool_after", "action": {"type": "http", "url": "https://x.com"}},
            {"event": "shutdown", "action": {"type": "subagent", "prompt": "检查代码"}},
        ]
    }
    rules = parse_hooks(data)
    assert len(rules) == 4
    assert [r.index for r in rules] == [1, 2, 3, 4]
    assert [r.event for r in rules] == [
        "startup",
        "round_start",
        "tool_after",
        "shutdown",
    ]
    # 缺省值
    for r in rules:
        assert r.run_once is False
        assert r.background is False
        assert r.timeout == DEFAULT_TIMEOUT
    # 无条件：conditions 空
    assert rules[0].conditions == []
    # http 缺省 method GET
    assert rules[2].action.method == "GET"


def test_parse_single_condition_map():
    data = {
        "hooks": [
            {
                "event": "tool_before",
                "if": {"field": "tool", "pattern": "run_command"},
                "action": {"type": "inject", "prompt": "x"},
            }
        ]
    }
    rules = parse_hooks(data)
    assert rules[0].match == "all"
    assert rules[0].conditions == [HookCondition("tool", "run_command")]


def test_parse_any_combo_and_full_fields():
    data = {
        "hooks": [
            {
                "event": "tool_before",
                "if": {
                    "any": [
                        {"field": "tool", "pattern": "run_command"},
                        {"field": "args.command", "pattern": "/^rm /"},
                    ]
                },
                "action": {
                    "type": "http",
                    "url": "https://x.com/hook",
                    "method": "POST",
                    "headers": {"X-T": "1"},
                    "body": '{"a": 1}',
                },
                "run_once": True,
                "timeout": 5,
            }
        ]
    }
    rules = parse_hooks(data)
    r = rules[0]
    assert r.match == "any"
    assert len(r.conditions) == 2
    assert r.run_once is True
    assert r.timeout == 5.0
    assert r.action == HookAction(
        type="http",
        url="https://x.com/hook",
        method="POST",
        headers={"X-T": "1"},
        body='{"a": 1}',
    )


# ----------------------------------------------------------------------
# 非法样例：每条断言 ConfigError 且信息含规则序号
# ----------------------------------------------------------------------


def _expect_error(data: dict, fragment: str):
    with pytest.raises(ConfigError) as exc:
        parse_hooks(data)
    assert "第 1 条" in str(exc.value)
    assert fragment in str(exc.value)


def test_invalid_event():
    _expect_error(
        {"hooks": [{"event": "tool_after_x", "action": {"type": "inject", "prompt": "x"}}]},
        "event 无效",
    )


def test_invalid_regex_pattern():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "tool_before",
                    "if": {"field": "tool", "pattern": "/[/"},
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "编译失败",
    )


def test_if_all_and_any_both():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "round_start",
                    "if": {"all": [], "any": []},
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "二选一",
    )


def test_if_all_empty_list():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "round_start",
                    "if": {"all": []},
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "非空",
    )


def test_if_nested_combo():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "round_start",
                    "if": {
                        "all": [
                            {
                                "any": [
                                    {"field": "event", "pattern": "round_start"},
                                ]
                            },
                        ]
                    },
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "不允许嵌套",
    )


def test_field_out_of_event_scope():
    # session_start 不允许 tool 字段
    _expect_error(
        {
            "hooks": [
                {
                    "event": "session_start",
                    "if": {"field": "tool", "pattern": "run_command"},
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "不支持条件字段",
    )


def test_command_missing_run():
    _expect_error(
        {"hooks": [{"event": "startup", "action": {"type": "command"}}]},
        "非空 run",
    )


def test_http_url_not_http_prefix():
    _expect_error(
        {
            "hooks": [
                {"event": "startup", "action": {"type": "http", "url": "file:///etc"}}
            ]
        },
        "http/https",
    )


def test_http_invalid_method():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "startup",
                    "action": {
                        "type": "http",
                        "url": "https://x.com",
                        "method": "PATCH",
                    },
                }
            ]
        },
        "method 无效",
    )


def test_tool_before_background_conflict():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "tool_before",
                    "background": True,
                    "action": {"type": "inject", "prompt": "x"},
                }
            ]
        },
        "不允许 background",
    )


@pytest.mark.parametrize("bad", [0, -1, "abc"])
def test_invalid_timeout(bad):
    _expect_error(
        {
            "hooks": [
                {
                    "event": "startup",
                    "timeout": bad,
                    "action": {"type": "command", "run": "true"},
                }
            ]
        },
        "正数",
    )


def test_run_once_not_bool():
    _expect_error(
        {
            "hooks": [
                {
                    "event": "startup",
                    "run_once": "yes",
                    "action": {"type": "command", "run": "true"},
                }
            ]
        },
        "布尔",
    )


# ----------------------------------------------------------------------
# 顶层结构与文件读取
# ----------------------------------------------------------------------


def test_no_hooks_key_returns_empty():
    assert parse_hooks({"providers": []}) == []


def test_empty_hooks_returns_empty():
    assert parse_hooks({"hooks": []}) == []


def test_load_hooks_missing_file(tmp_path):
    assert load_hooks(tmp_path / "nope.yaml") == []


def test_load_hooks_yaml_error(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("hooks: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_hooks(p)


def test_load_hooks_roundtrip(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "hooks": [
                    {
                        "event": "startup",
                        "action": {"type": "command", "run": "echo hi"},
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    rules = load_hooks(p)
    assert len(rules) == 1
    assert isinstance(rules[0], HookRule)
    assert rules[0].event == "startup"
