"""子 Agent 角色发现：四级目录扫描合并（ch16 F1）。

四级目录（优先级从高到低）：
1. 项目 `<根>/.mewcode/agents/`
2. 用户 `~/.mewcode/agents/`
3. 内置 `mewcode/agents/`（随包分发）
4. 插件 `~/.mewcode/plugins/*/agents/`

每个目录下两种形态：单文件角色 `*.md`；目录型角色 `*/AGENT.md`。
同名角色高优先级静默覆盖（与 skills 三级加载同款逻辑扩展为四级）；
同层内重名按路径排序、后者跳过并警告；解析失败的单个文件跳过并
警告——都不阻断整体。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mewcode.agents.model import AgentRole, parse_agent_file


@dataclass
class DiscoverResult:
    """角色发现结果：生效角色集与警告列表。"""

    roles: dict[str, AgentRole]  # name → 生效角色（覆盖后）
    warnings: list[str]


def _scan_dir(dir_path: Path, level: str) -> tuple[dict[str, AgentRole], list[str]]:
    """扫描一层目录：*.md 文件 + */AGENT.md 子目录；返回（角色表，警告）。

    同层重名只在本层 dict 内判定；跨层覆盖由调用方 update 完成。
    """
    roles: dict[str, AgentRole] = {}
    warnings: list[str] = []
    if not dir_path.is_dir():
        return roles, warnings
    entries: list[tuple[Path, Path | None]] = []
    for md in sorted(p for p in dir_path.iterdir() if p.is_file() and p.suffix == ".md"):
        entries.append((md, None))
    for sub in sorted(p for p in dir_path.iterdir() if p.is_dir()):
        entry = sub / "AGENT.md"
        if entry.is_file():
            entries.append((entry, sub))
    for path, _agent_dir in entries:
        role, error = parse_agent_file(path, level)
        if error is not None:
            warnings.append(f"角色文件 {path} 解析失败，已跳过: {error}")
            continue
        if role.name in roles:
            warnings.append(
                f"同层重名角色 {role.name!r}（{path}），已跳过"
                f"（保留先扫描的 {roles[role.name].source}）"
            )
            continue
        roles[role.name] = role
    return roles, warnings


def discover_agents(
    project_root: Path, user_dir: Path, builtin_dir: Path
) -> DiscoverResult:
    """扫描四级角色目录并合并；返回生效集与警告，不抛异常。"""
    result = DiscoverResult(roles={}, warnings=[])
    # 从低到高合并：update 直接覆盖，高级别的同名角色覆盖低级别
    plugin_root = user_dir / "plugins"
    plugin_dirs = sorted(plugin_root.glob("*/agents")) if plugin_root.is_dir() else []
    for plugin_agents in plugin_dirs:
        found, warnings = _scan_dir(plugin_agents, "plugin")
        result.warnings.extend(warnings)
        result.roles.update(found)
    for dir_path, level in (
        (builtin_dir, "builtin"),
        (user_dir / "agents", "user"),
        (project_root / ".mewcode" / "agents", "project"),
    ):
        found, warnings = _scan_dir(dir_path, level)
        result.warnings.extend(warnings)
        result.roles.update(found)
    return result
