"""三层指令加载与 @include 展开测试（ch11 F1）。"""

from pathlib import Path

from mewcode.memory.instructions import load_instructions


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_three_layers_joined_in_priority_order(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "MEWCODE.md", "项目根指令")
    _write(project / ".mewcode" / "instructions.md", "项目指令")
    _write(user / "instructions.md", "用户指令")
    result = load_instructions(project, user)
    assert result.text == "项目根指令\n\n项目指令\n\n用户指令"
    assert result.warnings == []


def test_missing_layer_skipped_silently(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(user / "instructions.md", "用户指令")
    result = load_instructions(project, user)
    assert result.text == "用户指令"
    assert result.warnings == []  # 缺失层不警告


def test_all_missing_returns_empty(tmp_path):
    result = load_instructions(tmp_path / "proj", tmp_path / "home")
    assert result.text == ""
    assert result.warnings == []


def test_corrupt_file_warns_and_continues(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    # 非法 UTF-8 字节 → 读取失败但存在 → 警告且不抛
    _write(project / "MEWCODE.md", "项目根指令")
    (project / ".mewcode" / "instructions.md").parent.mkdir(parents=True)
    (project / ".mewcode" / "instructions.md").write_bytes(b"\xff\xfe\x00bad")
    _write(user / "instructions.md", "用户指令")
    result = load_instructions(project, user)
    assert result.text == "项目根指令\n\n用户指令"
    assert len(result.warnings) == 1
    assert "instructions.md" in result.warnings[0]


# ---- @include 展开（T3）----

def test_include_inline_expansion(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "shared.md", "共享内容")
    _write(project / "MEWCODE.md", "开头\n@include shared.md\n结尾")
    result = load_instructions(project, user)
    assert result.text == "开头\n共享内容\n结尾"
    assert result.warnings == []


def test_include_relative_path_resolves_from_current_file(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "sub" / "nested.md", "嵌套内容")
    _write(project / "MEWCODE.md", "@include sub/nested.md")
    _write(project / "sub" / "b.md", "B 内容")
    _write(project / "sub" / "a.md", "@include b.md")  # 相对 sub/ 解析
    _write(project / ".mewcode" / "instructions.md", "@include ../sub/a.md")
    result = load_instructions(project, user)
    assert result.text == "嵌套内容\n\nB 内容"
    assert result.warnings == []


def test_include_cycle_terminates(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "b.md", "@include a.md\nB 内容")
    _write(project / "a.md", "@include b.md\nA 内容")
    _write(project / "MEWCODE.md", "@include a.md")
    result = load_instructions(project, user)
    # 不死循环；B 只被加载一次（A 中再 include B 时已 visited）
    assert "A 内容" in result.text
    assert result.warnings == []


def test_include_depth_limit(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "l3.md", "L3 内容")
    _write(project / "l2.md", "@include l3.md\nL2 内容")
    _write(project / "l1.md", "@include l2.md\nL1 内容")
    _write(project / "MEWCODE.md", "@include l1.md")
    result = load_instructions(project, user)
    # MEWCODE(1) → l1(2) → l2(3)；l2 再 include l3（第 4 层）被跳过
    assert "L1 内容" in result.text
    assert "L2 内容" in result.text
    assert "L3 内容" not in result.text
    assert any("超过" in w for w in result.warnings)


def test_include_outside_project_blocked(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    outside = tmp_path / "outside.md"
    _write(outside, "外部内容")
    _write(project / "MEWCODE.md", f"@include {outside}")
    result = load_instructions(project, user)
    assert "外部内容" not in result.text
    assert any("超出允许目录" in w for w in result.warnings)


def test_user_level_include_allowed_within_user_dir_only(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(user / "extra.md", "用户扩展")
    _write(user / "instructions.md", "@include extra.md")
    _write(project / "MEWCODE.md", "项目指令")
    result = load_instructions(project, user)
    assert "用户扩展" in result.text  # ~/.mewcode 内 include 正常
    # 用户级 include 项目目录内文件 → 越界拦截
    _write(project / "leak.md", "项目内容")
    _write(user / "instructions.md", f"@include {project / 'leak.md'}")
    result = load_instructions(project, user)
    assert "项目内容" not in result.text
    assert any("超出允许目录" in w for w in result.warnings)


def test_include_missing_target_warns(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "MEWCODE.md", "@include not_there.md\n正文")
    result = load_instructions(project, user)
    assert result.text == "正文"
    assert any("不存在" in w for w in result.warnings)


def test_include_quoted_path(tmp_path):
    project = tmp_path / "proj"
    user = tmp_path / "home" / ".mewcode"
    _write(project / "has space.md", "带空格内容")
    _write(project / "MEWCODE.md", '@include "has space.md"')
    result = load_instructions(project, user)
    assert result.text == "带空格内容"
