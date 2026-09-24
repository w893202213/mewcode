"""子 Agent 角色定义：AgentRole 数据模型与 frontmatter 解析校验（ch16 F1）。

角色文件格式（单文件 *.md；目录型 */AGENT.md）::

    ---
    name: code-reviewer       # 必填：唯一名字（非空、不含空白）
    description: 审查代码     # 必填：一句话说明
    tools:                    # 可选：工具白名单；缺省 = 不收窄
      - read_file
      - grep_code
    deny_tools:               # 可选：黑名单（再从结果集剔除）
      - run_command
    model: deepseek-flash     # 可选：None = 继承主对话 provider
    max_rounds: 20            # 可选：最大轮次；缺省 30
    permission: strict        # 可选：strict/default/accept；缺省 inherit
    ---
    （正文：子 Agent 的系统提示，伴随整个生命周期）

校验规则：字段类型不对 = 解析失败（返回 (None, 原因)），由调用方跳过
并警告；未知字段忽略（向前兼容）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# 权限模式合法值（None = inherit 父当前档位）
PERMISSION_VALUES = ("strict", "default", "accept")
# 最大轮次缺省（与主 Agent 迭代上限一致）
DEFAULT_MAX_ROUNDS = 30


@dataclass
class AgentRole:
    """一个子 Agent 角色的完整定义（解析产物，不可变）。"""

    name: str  # 唯一名字（frontmatter 权威，与文件名无关）
    description: str  # 一句话说明（工具错误提示展示）
    body: str  # 正文 = 子 Agent 系统提示（进自定义指令块）
    tools: tuple[str, ...] | None  # 工具白名单；None = 不收窄
    deny_tools: tuple[str, ...]  # 工具黑名单（再从结果集剔除）
    model: str | None  # None = 继承主对话 provider
    max_rounds: int  # 最大轮次；缺省 30
    permission: str | None  # strict/default/accept；None = inherit
    source: Path  # 文件路径（排错展示）
    level: str  # 来源层级 project / user / builtin / plugin


def _parse_frontmatter(content: str) -> tuple[dict, str | None]:
    """提取并校验 frontmatter；失败返回 ({}, 原因)。"""
    if not content.startswith("---"):
        return {}, "缺少 frontmatter（首行须为 ---）"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, "frontmatter 未闭合（缺少结尾 ---）"
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        return {}, f"frontmatter YAML 解析失败: {e}"
    if not isinstance(data, dict):
        return {}, "frontmatter 必须是键值映射"

    # name：必填、非空、不含空白
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return {}, "字段 name 必须是非空字符串"
    name = name.strip()
    if any(c.isspace() for c in name):
        return {}, f"字段 name 含空白: {name!r}"

    # description：必填、非空字符串
    description = data.get("description")
    if not isinstance(description, str) or not description.strip():
        return {}, "字段 description 必须是非空字符串"

    # tools：可选，字符串列表（None 允许 = 不收窄）
    tools = data.get("tools")
    if tools is not None and (
        not isinstance(tools, list)
        or not all(isinstance(t, str) and t for t in tools)
    ):
        return {}, "字段 tools 必须是非空字符串列表"

    # deny_tools：可选，字符串列表（None 允许）
    deny_tools = data.get("deny_tools")
    if deny_tools is not None and (
        not isinstance(deny_tools, list)
        or not all(isinstance(t, str) and t for t in deny_tools)
    ):
        return {}, "字段 deny_tools 必须是非空字符串列表"

    # model：可选，字符串
    model = data.get("model")
    if model is not None and not isinstance(model, str):
        return {}, "字段 model 必须是字符串"

    # max_rounds：可选，正整数（注意排除 bool）
    max_rounds = data.get("max_rounds", DEFAULT_MAX_ROUNDS)
    if (
        isinstance(max_rounds, bool)
        or not isinstance(max_rounds, int)
        or max_rounds <= 0
    ):
        return {}, f"字段 max_rounds 必须是正整数: {max_rounds!r}"

    # permission：可选，三值之一
    permission = data.get("permission")
    if permission is not None and permission not in PERMISSION_VALUES:
        return {}, f"字段 permission 无效: {permission!r}（可选: strict/default/accept）"

    return {
        "name": name,
        "description": description.strip(),
        "tools": tuple(tools) if tools else None,
        "deny_tools": tuple(deny_tools) if deny_tools else (),
        "model": model,
        "max_rounds": max_rounds,
        "permission": permission,
    }, None


def parse_agent_file(
    path: Path, level: str
) -> tuple[AgentRole | None, str | None]:
    """解析单个角色文件；成功返回 (AgentRole, None)，失败返回 (None, 原因)。

    level 为来源层级字符串（project/user/builtin/plugin）。本函数不抛异常。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, str(e)
    meta, error = _parse_frontmatter(content)
    if error is not None:
        return None, error
    body = content.split("---", 2)[2].strip()
    return (
        AgentRole(
            name=meta["name"],
            description=meta["description"],
            body=body,
            tools=meta["tools"],
            deny_tools=meta["deny_tools"],
            model=meta["model"],
            max_rounds=meta["max_rounds"],
            permission=meta["permission"],
            source=path,
            level=level,
        ),
        None,
    )
