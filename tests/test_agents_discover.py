"""子 Agent 角色发现测试（ch16 F1）：四级覆盖、重名、坏文件跳过。"""

from pathlib import Path

from mewcode.agents.discover import discover_agents

BUILTIN = Path(__file__).resolve().parent.parent / "mewcode" / "agents"

ROLE_TMPL = "---\nname: {name}\ndescription: {desc}\n---\n{body}"


def _write(path: Path, name: str, desc: str, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ROLE_TMPL.format(name=name, desc=desc, body=body), encoding="utf-8")
    return path


def test_four_level_precedence(tmp_path):
    # 插件/用户/项目三级各放同名角色；内置层用真实包目录
    _write(tmp_path / "plugins" / "p1" / "agents" / "r.md", "r", "插件", "插件版")
    _write(tmp_path / "agents" / "r.md", "r", "用户", "用户版")
    _write(tmp_path / ".mewcode" / "agents" / "r.md", "r", "项目", "项目版")
    result = discover_agents(tmp_path, tmp_path, BUILTIN)
    assert result.roles["r"].level == "project"
    assert result.roles["r"].body == "项目版"


def test_builtin_roles_present_when_unshadowed(tmp_path):
    result = discover_agents(tmp_path, tmp_path, BUILTIN)
    assert "general" in result.roles
    assert "code-reviewer" in result.roles
    assert result.roles["general"].level == "builtin"


def test_same_level_duplicate_skips_later(tmp_path):
    _write(tmp_path / "agents" / "a.md", "r", "甲", "甲版")
    _write(tmp_path / "agents" / "b.md", "r", "乙", "乙版")
    result = discover_agents(tmp_path, tmp_path, BUILTIN)
    assert result.roles["r"].body == "甲版"  # 先扫描的保留
    assert any("同层重名" in w for w in result.warnings)


def test_bad_file_skipped_with_warning(tmp_path):
    bad = tmp_path / "agents" / "bad.md"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("没有 frontmatter", encoding="utf-8")
    _write(tmp_path / "agents" / "ok.md", "ok", "好的", "正文")
    result = discover_agents(tmp_path, tmp_path, BUILTIN)
    assert "ok" in result.roles
    assert any("bad.md" in w and "解析失败" in w for w in result.warnings)


def test_dir_style_agent(tmp_path):
    _write(tmp_path / "agents" / "d" / "AGENT.md", "d", "目录型", "目录正文")
    result = discover_agents(tmp_path, tmp_path, BUILTIN)
    assert result.roles["d"].body == "目录正文"


def test_no_dirs_returns_only_builtin(tmp_path):
    empty = tmp_path / "empty"
    result = discover_agents(empty, empty, BUILTIN)
    assert set(result.roles) == {"general", "code-reviewer"}
    assert result.warnings == []
