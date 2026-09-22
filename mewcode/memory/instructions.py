"""项目指令文件：三层加载与 @include 展开（ch11 F1）。

三层文件（优先级从高到低）：
1. 项目根 MEWCODE.md
2. 项目根 .mewcode/instructions.md
3. 用户目录 ~/.mewcode/instructions.md

缺失层静默跳过；读取失败打印警告不阻断启动；@include 限嵌套深度、
visited 集合防环、越界路径拦截（项目级限于项目根内，用户级限于
用户目录内）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# @include 最大嵌套深度（顶层文件为第 1 层）
MAX_INCLUDE_DEPTH = 3


@dataclass
class InstructionResult:
    """加载结果：拼接文本 + 中文警告列表。"""

    text: str
    warnings: list[str] = field(default_factory=list)


def _read_text_safe(path: Path) -> tuple[str, str | None]:
    """读文本文件：返回（内容，错误消息）；失败时内容为空串、错误非 None。"""
    try:
        return path.read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return "", "文件不存在"
    except (OSError, UnicodeDecodeError) as e:
        return "", str(e)


def load_instructions(project_root: Path, user_dir: Path) -> InstructionResult:
    """按优先级加载三层指令文件、展开 @include 并拼接；不抛异常。"""
    warnings: list[str] = []
    visited: set[Path] = set()
    parts: list[str] = []
    layers = (
        (project_root / "MEWCODE.md", project_root, "project"),
        (project_root / ".mewcode" / "instructions.md", project_root, "project"),
        (user_dir / "instructions.md", user_dir, "user"),
    )
    for path, scope_root, scope in layers:
        if not path.exists():
            continue  # 不存在 → 静默跳过
        text = _expand(path, scope_root, scope, depth=1, visited=visited, warnings=warnings)
        if text.strip():
            parts.append(text.strip())
    return InstructionResult(text="\n\n".join(parts), warnings=warnings)


def _expand(
    path: Path,
    scope_root: Path,
    scope: str,
    depth: int,
    visited: set[Path],
    warnings: list[str],
) -> str:
    """递归展开一个指令文件的 @include；返回展开后的文本（失败返回空串）。"""
    content, error = _read_text_safe(path)
    if error is not None:
        warnings.append(f"指令文件 {path} 读取失败，已跳过: {error}")
        return ""
    resolved = path.resolve()
    visited.add(resolved)
    lines: list[str] = []
    for line in content.splitlines():
        include_target = _parse_include(line)
        if include_target is None:
            lines.append(line)
            continue
        target = (path.parent / include_target).resolve()
        if target in visited:
            continue  # 已加载过：静默跳过（防环）
        if depth + 1 > MAX_INCLUDE_DEPTH:
            warnings.append(f"include 嵌套超过 {MAX_INCLUDE_DEPTH} 层，已跳过: {include_target}")
            continue
        try:
            allowed = target.is_relative_to(scope_root.resolve())
        except (OSError, ValueError):
            allowed = False
        if not allowed:
            warnings.append(f"include 目标超出允许目录，已跳过: {include_target}")
            continue
        if not target.exists():
            warnings.append(f"include 目标不存在: {include_target}")
            continue
        lines.append(_expand(target, scope_root, scope, depth + 1, visited, warnings))
    return "\n".join(lines)


def _parse_include(line: str) -> str | None:
    """行首（允许前导空白）@include <路径> → 返回路径（去引号）；非 include 行返回 None。"""
    stripped = line.strip()
    if not stripped.startswith("@"):
        return None
    parts = stripped.split(None, 1)
    if len(parts) != 2 or parts[0] != "@include":
        return None
    target = parts[1].strip()
    if len(target) >= 2 and target[0] == target[-1] and target[0] in "\"'":
        target = target[1:-1]
    return target or None
