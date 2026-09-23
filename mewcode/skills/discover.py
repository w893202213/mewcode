"""技能发现：三级扫描、优先级覆盖、白名单启动校验（ch13 F2/F5）。

三级目录（优先级从高到低）：
1. 项目 .mewcode/skills/
2. 用户 ~/.mewcode/skills/
3. 内置 mewcode/skills/（随包分发）

每个目录下两种形态：单文件技能 *.md；目录型技能 */SKILL.md。
同名技能高优先级覆盖低优先级（静默）；同一级内重名按路径排序、
后者跳过并警告；解析失败的单个文件跳过并警告——都不阻断整体。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from mewcode.skills.model import Skill, parse_skill_file


@dataclass
class DiscoverResult:
    """扫描结果：生效技能集（同名已覆盖）+ 中文警告列表。"""

    skills: dict[str, Skill] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def discover_skills(
    project_root: Path, user_dir: Path, builtin_dir: Path
) -> DiscoverResult:
    """扫描三级技能目录并合并；返回生效集与警告，不抛异常。"""
    result = DiscoverResult()
    # 从低到高合并：高级别的同名技能直接覆盖低级别（静默）
    layers = (
        (builtin_dir, "builtin"),
        (user_dir / "skills", "user"),
        (project_root / ".mewcode" / "skills", "project"),
    )
    for dir_path, level in layers:
        if not dir_path.is_dir():
            continue  # 层级目录缺失 → 静默跳过
        found, warnings = _scan_dir(dir_path, level)
        result.warnings.extend(warnings)
        result.skills.update(found)
    return result


def _scan_dir(dir_path: Path, level: str) -> tuple[dict[str, Skill], list[str]]:
    """扫描一层目录：*.md 文件 + */SKILL.md 子目录；返回（技能表，警告）。"""
    skills: dict[str, Skill] = {}
    warnings: list[str] = []
    entries: list[tuple[Path, Path | None]] = []  # (入口文件, 目录型目录或 None)
    for md in sorted(dir_path.glob("*.md")):
        entries.append((md, None))
    for sub in sorted(p for p in dir_path.iterdir() if p.is_dir()):
        entry = sub / "SKILL.md"
        if entry.is_file():
            entries.append((entry, sub))
    for path, skill_dir in entries:
        skill, error = parse_skill_file(path, level, dir=skill_dir)
        if error is not None:
            warnings.append(f"技能文件 {path} 解析失败，已跳过: {error}")
            continue
        if skill.name in skills:
            warnings.append(
                f"同层重名技能 {skill.name!r}（{path}），已跳过（保留先扫描的 {skills[skill.name].source}）"
            )
            continue
        skills[skill.name] = skill
    return skills, warnings


def validate_whitelists(skills: dict[str, Skill], registry_names: set[str]) -> list[str]:
    """校验生效技能的白名单：引用不存在的工具 → 返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    for name in sorted(skills):
        skill = skills[name]
        if skill.tools is None:
            continue
        for tool in skill.tools:
            if tool not in registry_names:
                problems.append(f"技能 {name} 白名单引用未知工具: {tool}")
    return problems


def skills_summary(skills: dict[str, Skill]) -> str:
    """技能清单文本（启动注入 stable 前缀）：`- name: description` 按名排序。"""
    if not skills:
        return ""
    lines = ["【技能】"]
    for name in sorted(skills):
        lines.append(f"- {name}: {skills[name].description}")
    return "\n".join(lines)
