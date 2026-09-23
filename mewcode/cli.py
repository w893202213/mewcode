"""CLI 入口：解析参数、装配组件、启动 TUI。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mewcode.commands.builtins import build_registry
from mewcode.commands.registry import CommandConflictError
from mewcode.config import (
    ConfigError,
    load_config,
    load_custom_instructions,
    load_permission_mode,
    select_profile,
)
from mewcode.mcp import McpManager, load_mcp_servers
from mewcode.memory import build_memory_context, load_instructions
from mewcode.permissions import PermissionGate, RuleSet
from mewcode.provider import create_provider
from mewcode.skills import BUILTIN_SKILLS_DIR
from mewcode.skills.discover import (
    discover_skills,
    skills_summary,
    validate_whitelists,
)
from mewcode.skills.manager import SkillManager
from mewcode.skills.use_skill import UseSkillTool
from mewcode.tools import create_registry
from mewcode.tui.app import run

# 配置文件默认路径
DEFAULT_CONFIG_PATH = Path.home() / ".mewcode" / "config.yaml"
# 用户目录（三层指令与用户级记忆的根）
USER_DIR = Path.home() / ".mewcode"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mewcode",
        description="MewCode —— 终端 AI 编程助手",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="使用哪份供应商配置（缺省用配置文件中的第一份）",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"配置文件路径（缺省 {DEFAULT_CONFIG_PATH}）",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help="恢复会话：不带 ID 恢复最近一个会话，可指定会话 ID",
    )
    return parser.parse_args(argv)


def _build_gate(config_path: str) -> PermissionGate:
    """装配权限闸门：三规则文件合并读取 + 启动档位（ch07 F3/F4）。

    规则文件损坏或 permission_mode 非法抛 ConfigError（fail-closed）。
    """
    root = Path.cwd()
    local_path = root / ".mewcode" / "permissions.local.yaml"
    rules = RuleSet()
    rules.load_from(
        [
            Path.home() / ".mewcode" / "permissions.yaml",
            root / ".mewcode" / "permissions.yaml",
            local_path,
        ],
        local_path,
    )
    return PermissionGate(root.resolve(), rules, load_permission_mode(config_path))


def main(argv: list[str] | None = None) -> None:
    """入口：配置错误打印到 stderr 并以非零码退出。

    装配顺序（ch08 + ch11 F6 + ch12 + ch13）：providers → 闸门 → 内置工具
    → MCP 发现注册（内部警告不抛）→ 规则工具名统一校验 → 命令注册中心
    （冲突即启动失败）→ 技能发现（警告不阻断）→ use_skill 注册 →
    白名单启动校验（未知工具即退出）→ 三层指令加载 → 记忆上下文
    （创建/恢复会话 + 过期清理）→ TUI；退出时统一关闭全部 MCP 连接
    （终止子进程）。
    """
    args = _parse_args(argv)
    try:
        configs = load_config(args.config)
        config = select_profile(configs, args.profile)
        gate = _build_gate(args.config)
        mcp_servers = load_mcp_servers(
            args.config, Path.cwd() / ".mewcode" / "config.yaml"
        )
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    provider = create_provider(config)
    registry = create_registry()
    manager = McpManager(mcp_servers)
    manager.discover_and_register(registry)
    try:
        gate.validate_tools({tool.name for tool in registry.list()})
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        manager.shutdown()  # 清理已启动的 MCP 连接
        sys.exit(1)

    # ch12 F1：命令注册中心——撞名直接启动失败，不等运行时再炸
    try:
        command_registry = build_registry()
    except CommandConflictError as e:
        print(f"命令注册冲突：\n{e}", file=sys.stderr)
        manager.shutdown()  # 清理已启动的 MCP 连接
        sys.exit(1)

    # ch13 F2/F5：技能发现（警告不阻断）→ 管理器 → use_skill 注册 →
    # 白名单启动校验（引用不存在的工具立即退出，N6）
    project_root = Path.cwd().resolve()
    discovered = discover_skills(project_root, USER_DIR, BUILTIN_SKILLS_DIR)
    for warning in discovered.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    skill_manager = SkillManager(discovered.skills)
    registry.register(UseSkillTool(skill_manager))
    problems = validate_whitelists(
        discovered.skills, {tool.name for tool in registry.list()}
    )
    if problems:
        for problem in problems:
            print(f"配置错误: {problem}", file=sys.stderr)
        manager.shutdown()
        sys.exit(1)

    # ch11 F1：三层指令加载（警告不阻断），config 字段作为用户级最末段
    instructions = load_instructions(project_root, USER_DIR)
    for warning in instructions.warnings:
        print(f"警告: {warning}", file=sys.stderr)
    custom_instructions = "\n\n".join(
        p
        for p in (instructions.text, load_custom_instructions(args.config))
        if p.strip()
    )

    # ch11 F6：记忆上下文（会话创建/恢复 + 过期清理）
    memory = build_memory_context(project_root, USER_DIR, args.resume)
    if memory.resume_summary:
        print(memory.resume_summary)

    try:
        run(
            provider,
            config,
            registry,
            custom_instructions,
            gate,
            memory=memory,
            command_registry=command_registry,
            skill_manager=skill_manager,
            skills_summary=skills_summary(discovered.skills),
        )
    finally:
        manager.shutdown()
