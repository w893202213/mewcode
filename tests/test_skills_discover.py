"""技能发现测试（ch13 F2/F5）：三级扫描、优先级覆盖、白名单启动校验。"""

from pathlib import Path

from mewcode.skills.discover import (
    discover_skills,
    skills_summary,
    validate_whitelists,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _skill(name: str, description: str = "说明", extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: {description}{extra}\n---\n正文 {name}\n"


def _dirs(tmp_path):
    return (
        tmp_path / "proj",
        tmp_path / "home" / ".mewcode",
        tmp_path / "builtin",
    )


def test_three_levels_priority_override(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "demo.md", _skill("demo", "内置版"))
    _write(user / "skills" / "demo.md", _skill("demo", "用户版"))
    _write(project / ".mewcode" / "skills" / "demo.md", _skill("demo", "项目版"))
    result = discover_skills(project, user, builtin)
    assert result.warnings == []
    assert result.skills["demo"].description == "项目版"
    assert result.skills["demo"].level == "project"


def test_user_overrides_builtin(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "demo.md", _skill("demo", "内置版"))
    _write(user / "skills" / "demo.md", _skill("demo", "用户版"))
    result = discover_skills(project, user, builtin)
    assert result.warnings == []
    assert result.skills["demo"].description == "用户版"
    assert result.skills["demo"].level == "user"


def test_missing_layer_dirs_silently_skipped(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "demo.md", _skill("demo"))
    result = discover_skills(project, user, builtin)
    assert result.warnings == []
    assert "demo" in result.skills


def test_same_level_duplicate_skips_later(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "a.md", _skill("dup", "第一"))
    _write(builtin / "b.md", _skill("dup", "第二"))
    result = discover_skills(project, user, builtin)
    assert result.skills["dup"].description == "第一"  # 按路径排序，a.md 在前
    assert len(result.warnings) == 1
    assert "重名" in result.warnings[0]


def test_bad_file_skipped_others_ok(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "good.md", _skill("good"))
    _write(builtin / "bad.md", "没有 frontmatter\n")
    _write(builtin / "bad2.md", "---\nname: x\n---\n")  # 缺 description
    result = discover_skills(project, user, builtin)
    assert list(result.skills) == ["good"]
    assert len(result.warnings) == 2
    assert all("解析失败" in w for w in result.warnings)


def test_directory_skill_discovered_with_dir(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "pack" / "SKILL.md", _skill("pack", "目录型"))
    _write(builtin / "pack" / "references" / "ref.md", "参考")
    result = discover_skills(project, user, builtin)
    assert "pack" in result.skills
    assert result.skills["pack"].dir == builtin / "pack"


def test_project_overrides_directory_skill(tmp_path):
    project, user, builtin = _dirs(tmp_path)
    _write(builtin / "pack" / "SKILL.md", _skill("pack", "内置目录型"))
    _write(project / ".mewcode" / "skills" / "pack.md", _skill("pack", "项目单文件"))
    result = discover_skills(project, user, builtin)
    assert result.skills["pack"].description == "项目单文件"


def test_validate_whitelists_ok_and_unknown():
    from mewcode.skills.model import Skill, SkillMode

    skill_ok = Skill(
        name="a", description="d", body="b", tools=("run_command",), mode=SkillMode.SHARED,
        history=0, model=None, dir=None, source=Path("a.md"), level="project",
    )
    skill_bad = Skill(
        name="b", description="d", body="b", tools=("no_such_tool", "run_command"),
        mode=SkillMode.SHARED, history=0, model=None, dir=None, source=Path("b.md"),
        level="project",
    )
    skill_none = Skill(
        name="c", description="d", body="b", tools=None, mode=SkillMode.SHARED,
        history=0, model=None, dir=None, source=Path("c.md"), level="project",
    )
    problems = validate_whitelists(
        {"a": skill_ok, "b": skill_bad, "c": skill_none}, {"run_command"}
    )
    assert problems == ["技能 b 白名单引用未知工具: no_such_tool"]


def test_skills_summary_sorted():
    from mewcode.skills.model import Skill, SkillMode

    def make(name, desc):
        return Skill(
            name=name, description=desc, body="", tools=None, mode=SkillMode.SHARED,
            history=0, model=None, dir=None, source=Path(f"{name}.md"), level="builtin",
        )

    text = skills_summary({"b": make("b", "技能B"), "a": make("a", "技能A")})
    assert text == "【技能】\n- a: 技能A\n- b: 技能B"
    assert skills_summary({}) == ""
