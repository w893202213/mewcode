"""MCP Server 配置层：读取、校验、${VAR} 展开与两层合并。

YAML 顶层 `mcp_servers` 是一个 map，key 是 Server 名，value 是传输配置：:

    mcp_servers:
      demo:                       # stdio：有 command
        command: python
        args: [scripts/mock_mcp_server.py]
        env:                      # 值支持 ${VAR} 展开
          MOCK_PAGE_SIZE: "${MOCK_PAGE_SIZE}"
      remote:                     # http：有 url
        url: https://example.com/mcp
        headers:                  # 值支持 ${VAR} 展开
          Authorization: "Bearer ${TOKEN}"

用户级（--config 指向的文件）与项目级（启动目录 .mewcode/config.yaml）
按键合并，同名 Server 整条目覆盖（项目级盖用户级），保留原位置。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mewcode.config import ConfigError

# ${VAR} 变量引用模式（只匹配一个整体变量，不做拼接）
_VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_var(value: str) -> str:
    """把字符串中的 ${VAR} 展开为环境变量值；未定义的变量抛 ConfigError。

    展开后仍是字符串（不做类型转换）；不匹配 ${...} 的字符原样保留。
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in os.environ:
            raise ConfigError(f"MCP 配置引用了未定义的环境变量: ${{{name}}}")
        return os.environ[name]

    return _VAR_PATTERN.sub(_sub, value)


@dataclass
class McpServerConfig:
    """一份 MCP Server 配置，对应两层合并后 mcp_servers 的一项。"""

    name: str  # Server 名（map 的 key）
    transport: str  # "stdio" | "http"
    command: str | None = None  # stdio：可执行命令（必填）
    args: list[str] = field(default_factory=list)  # stdio：参数（缺省空列表）
    env: dict[str, str] = field(default_factory=dict)  # stdio：额外环境变量（已展开）
    url: str | None = None  # http：端点（必填）
    headers: dict[str, str] = field(default_factory=dict)  # http：额外请求头（已展开）


def _parse_env(item: dict, server_name: str) -> dict[str, str]:
    """校验并展开 env map（值必须是字符串，展开后仍是字符串）。"""
    env = item.get("env", {})
    if not isinstance(env, dict):
        raise ConfigError(f"MCP Server {server_name!r} 的 env 必须是映射")
    expanded: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(value, str):
            raise ConfigError(f"MCP Server {server_name!r} 的 env.{key!s} 必须是字符串")
        expanded[str(key)] = expand_var(value)
    return expanded


def _parse_headers(item: dict, server_name: str) -> dict[str, str]:
    """校验并展开 headers map（值必须是字符串，展开后仍是字符串）。"""
    headers = item.get("headers", {})
    if not isinstance(headers, dict):
        raise ConfigError(f"MCP Server {server_name!r} 的 headers 必须是映射")
    expanded: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(value, str):
            raise ConfigError(
                f"MCP Server {server_name!r} 的 headers.{key!s} 必须是字符串"
            )
        expanded[str(key)] = expand_var(value)
    return expanded


def _parse_server(name: str, item: object) -> McpServerConfig:
    """校验并展开一条 Server 配置；非法抛 ConfigError（指明 Server 名与字段）。"""
    if not isinstance(item, dict):
        raise ConfigError(
            f"MCP Server {name!r} 配置格式错误: 需要 command（stdio）或 url（http）字段"
        )
    has_command = item.get("command") is not None
    has_url = item.get("url") is not None
    if has_command and has_url:
        raise ConfigError(
            f"MCP Server {name!r} 同时给出 command 与 url: 只能二选一（stdio 或 http）"
        )
    if not has_command and not has_url:
        raise ConfigError(f"MCP Server {name!r} 缺少 command 或 url 字段")

    if has_command:
        command = item["command"]
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"MCP Server {name!r} 的 command 必须是非空字符串")
        args = item.get("args", [])
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
            raise ConfigError(f"MCP Server {name!r} 的 args 必须是字符串列表")
        return McpServerConfig(
            name=name,
            transport="stdio",
            command=command.strip(),
            args=list(args),
            env=_parse_env(item, name),
        )

    url = item["url"]
    if not isinstance(url, str) or not url.strip():
        raise ConfigError(f"MCP Server {name!r} 的 url 必须是非空字符串")
    return McpServerConfig(
        name=name,
        transport="http",
        url=url.strip(),
        headers=_parse_headers(item, name),
    )


def _load_layer(path: str | os.PathLike) -> dict[str, object]:
    """读一个 YAML 文件的 mcp_servers map；文件缺失/字段缺失返回空 dict。

    文件本身损坏（YAML 语法错误）或 mcp_servers 存在但不是 map 时
    抛 ConfigError（fail-closed，与 load_config 口径一致）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件格式错误: {e}")
    if not isinstance(data, dict):
        return {}
    if "mcp_servers" not in data:
        return {}
    servers = data["mcp_servers"]
    if not isinstance(servers, dict):
        raise ConfigError(f"配置文件 {path}: mcp_servers 必须是映射（Server 名 → 配置）")
    return dict(servers)


def load_mcp_servers(
    user_path: str | os.PathLike, project_path: str | os.PathLike
) -> list[McpServerConfig]:
    """两层合并加载 MCP Server 配置：用户级先、项目级后，同名整条目覆盖。

    同名覆盖保留用户级中的原位置；项目级新增的排在末尾。
    两份都没有 mcp_servers 字段时返回空列表（MCP 功能不启用）。
    """
    merged: dict[str, object] = {}
    for layer_path in (user_path, project_path):
        for name, item in _load_layer(layer_path).items():
            merged[str(name)] = item
    return [_parse_server(name, item) for name, item in merged.items()]
