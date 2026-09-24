"""子 Agent 角色解析测试（ch16 F1）：frontmatter 校验与缺省值。"""

from mewcode.agents.model import (
    DEFAULT_MAX_ROUNDS,
    AgentRole,
    parse_agent_file,
)


def _write_role(tmp_path, name: str, content: str):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


FULL = """---
name: code-reviewer
description: 审查代码
tools:
  - read_file
  - grep_code
deny_tools:
  - run_command
model: deepseek-flash
max_rounds: 20
permission: strict
---

你是代码审查员，找出 bug 与简化机会。
"""


def test_parse_full_fields(tmp_path):
    role, err = parse_agent_file(_write_role(tmp_path, "r.md", FULL), "project")
    assert err is None
    assert isinstance(role, AgentRole)
    assert role.name == "code-reviewer"
    assert role.description == "审查代码"
    assert role.tools == ("read_file", "grep_code")
    assert role.deny_tools == ("run_command",)
    assert role.model == "deepseek-flash"
    assert role.max_rounds == 20
    assert role.permission == "strict"
    assert role.level == "project"
    assert role.body == "你是代码审查员，找出 bug 与简化机会。"


def test_parse_minimal_defaults(tmp_path):
    content = "---\nname: general\ndescription: 通用助手\n---\n\n完成即停。\n"
    role, err = parse_agent_file(_write_role(tmp_path, "g.md", content), "builtin")
    assert err is None
    assert role.name == "general"
    assert role.tools is None
    assert role.deny_tools == ()
    assert role.model is None
    assert role.max_rounds == DEFAULT_MAX_ROUNDS
    assert role.permission is None
    # 正文保留（strip 后仍含内部空行与末尾换行语义由 strip 决定）
    assert role.body == "完成即停。"


def test_missing_frontmatter(tmp_path):
    role, err = parse_agent_file(_write_role(tmp_path, "x.md", "没有 frontmatter"), "user")
    assert role is None
    assert "frontmatter" in err


def test_missing_name(tmp_path):
    role, err = parse_agent_file(
        _write_role(tmp_path, "x.md", "---\ndescription: d\n---\n正文"), "user"
    )
    assert role is None
    assert "name" in err


def test_name_with_whitespace(tmp_path):
    role, err = parse_agent_file(
        _write_role(tmp_path, "x.md", "---\nname: a b\ndescription: d\n---\n正文"),
        "user",
    )
    assert role is None
    assert "空白" in err


def test_missing_description(tmp_path):
    role, err = parse_agent_file(
        _write_role(tmp_path, "x.md", "---\nname: r\n---\n正文"), "user"
    )
    assert role is None
    assert "description" in err


def test_bad_tools_type(tmp_path):
    role, err = parse_agent_file(
        _write_role(
            tmp_path,
            "x.md",
            "---\nname: r\ndescription: d\ntools: not-a-list\n---\n正文",
        ),
        "user",
    )
    assert role is None
    assert "tools" in err


def test_bad_permission(tmp_path):
    role, err = parse_agent_file(
        _write_role(
            tmp_path,
            "x.md",
            "---\nname: r\ndescription: d\npermission: everything\n---\n正文",
        ),
        "user",
    )
    assert role is None
    assert "permission" in err


def test_bad_max_rounds(tmp_path):
    for bad in (0, -1, "ten", True):
        role, err = parse_agent_file(
            _write_role(
                tmp_path,
                "x.md",
                f"---\nname: r\ndescription: d\nmax_rounds: {bad!r}\n---\n正文",
            ),
            "user",
        )
        assert role is None
        assert "max_rounds" in err


def test_unknown_fields_ignored(tmp_path):
    role, err = parse_agent_file(
        _write_role(
            tmp_path,
            "x.md",
            "---\nname: r\ndescription: d\nfuture_field: 1\n---\n正文",
        ),
        "user",
    )
    assert err is None
    assert role.name == "r"
