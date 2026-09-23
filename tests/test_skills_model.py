"""技能数据模型测试（ch13 F1）：frontmatter 解析校验与参数占位符替换。"""

from pathlib import Path

from mewcode.skills.model import SkillMode, parse_skill_file, substitute_args


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _minimal(name="demo", description="演示技能"):
    return (
        f"---\nname: {name}\ndescription: {description}\n---\n"
        "正文 SOP 指令。\n"
    )


def test_full_fields_parsed(tmp_path):
    path = tmp_path / "full.md"
    _write(
        path,
        "---\n"
        "name: commit\n"
        "description: 提交代码\n"
        "tools: [run_command, read_file]\n"
        "mode: independent\n"
        "history: 3\n"
        "model: claude-sonnet\n"
        "---\n"
        "1. git status 查看改动\n"
        "2. 生成提交信息并提交（参数: $ARGS）\n",
    )
    skill, error = parse_skill_file(path, "project")
    assert error is None
    assert skill.name == "commit"
    assert skill.description == "提交代码"
    assert skill.tools == ("run_command", "read_file")
    assert skill.mode is SkillMode.INDEPENDENT
    assert skill.history == 3
    assert skill.model == "claude-sonnet"
    assert skill.dir is None
    assert skill.source == path
    assert skill.level == "project"
    assert "$ARGS" in skill.body  # 正文原样保留，激活时再替换


def test_defaults_applied(tmp_path):
    path = tmp_path / "mini.md"
    _write(path, _minimal())
    skill, error = parse_skill_file(path, "user", dir=path.parent)
    assert error is None
    assert skill.tools is None
    assert skill.mode is SkillMode.SHARED
    assert skill.history == 0
    assert skill.model is None
    assert skill.dir == path.parent  # 目录型技能由调用方传入 dir


def test_unknown_fields_ignored(tmp_path):
    path = tmp_path / "extra.md"
    _write(path, "---\nname: demo\ndescription: 说明\nversion: 2\nfoo: bar\n---\n正文\n")
    skill, error = parse_skill_file(path, "builtin")
    assert error is None
    assert skill.name == "demo"


def test_name_with_whitespace_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, _minimal(name="my skill"))
    _, error = parse_skill_file(path, "project")
    assert error is not None and "name" in error


def test_missing_name_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\ndescription: 说明\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "name" in error


def test_missing_description_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "description" in error


def test_empty_description_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\ndescription: '  '\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "description" in error


def test_tools_wrong_type_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\ndescription: 说明\ntools: run_command\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "tools" in error


def test_mode_invalid_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\ndescription: 说明\nmode: solo\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "mode" in error


def test_history_negative_and_bool_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\ndescription: 说明\nhistory: -1\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "history" in error
    _write(path, "---\nname: demo\ndescription: 说明\nhistory: true\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "history" in error


def test_model_wrong_type_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\ndescription: 说明\nmodel: 123\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "model" in error


def test_no_frontmatter_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "正文但没有 frontmatter\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "frontmatter" in error


def test_unclosed_frontmatter_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: demo\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "frontmatter" in error


def test_bad_yaml_rejected(tmp_path):
    path = tmp_path / "bad.md"
    _write(path, "---\nname: [未闭合\n---\n正文\n")
    _, error = parse_skill_file(path, "project")
    assert error is not None and "YAML" in error


def test_unreadable_file_reports_reason(tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe\x00bad")
    _, error = parse_skill_file(path, "project")
    assert error is not None


def test_substitute_args_both_placeholders():
    body = "参数: $ARGUMENTS 与 $ARGS 都替换"
    assert substitute_args(body, "修复 bug") == "参数: 修复 bug 与 修复 bug 都替换"


def test_substitute_args_empty_replaces_with_empty():
    assert substitute_args("内容[$ARGS]", "") == "内容[]"


def test_substitute_args_order_no_prefix_clobber():
    # 先替换长占位符 $ARGUMENTS，再替换 $ARGS——不留 $ARGUMENTS 尾巴
    assert substitute_args("$ARGUMENTS", "x") == "x"
