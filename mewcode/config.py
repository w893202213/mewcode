"""配置层：读取、校验并选择供应商配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

# 支持的协议白名单
SUPPORTED_PROTOCOLS = {"anthropic", "openai"}


class ConfigError(Exception):
    """配置错误，消息一律中文、可读。"""


@dataclass
class ProviderConfig:
    """一份供应商配置，对应 YAML 中 providers 列表的一项。"""

    name: str  # 供应商标识名，方便区分多个配置
    protocol: str  # 走哪家协议: anthropic | openai
    model: str  # 指定模型
    base_url: str  # 请求地址
    api_key: str  # 认证密钥（绝不打印回显）
    thinking: bool = False  # 是否启用扩展思考


def load_config(path: str | os.PathLike) -> list[ProviderConfig]:
    """从 YAML 文件读取全部供应商配置，逐份校验。

    顶层结构::

        providers:
          - name: claude
            protocol: anthropic
            model: claude-...
            base_url: https://api.anthropic.com
            api_key: sk-...
            thinking: true

    校验失败抛 ConfigError，指明第几份配置缺什么。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"配置文件不存在: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件格式错误: {e}")

    if not isinstance(data, dict) or "providers" not in data:
        raise ConfigError("配置文件格式错误: 顶层需要 providers 列表")
    items = data["providers"]
    if not isinstance(items, list) or not items:
        raise ConfigError("配置文件格式错误: providers 必须是非空列表")

    configs: list[ProviderConfig] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ConfigError(
                f"第 {i} 份配置格式错误: 需要 name/protocol/model/base_url/api_key 字段"
            )
        # 必填字段检查
        for field_name in ("name", "protocol", "model", "base_url", "api_key"):
            value = item.get(field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise ConfigError(f"第 {i} 份配置缺少必填字段: {field_name}")
        protocol = item["protocol"].strip()
        if protocol not in SUPPORTED_PROTOCOLS:
            raise ConfigError(
                f"第 {i} 份配置的 protocol 不支持: {protocol!r}（可选: anthropic, openai）"
            )
        thinking = item.get("thinking", False)
        if not isinstance(thinking, bool):
            raise ConfigError(f"第 {i} 份配置的 thinking 必须是布尔值 (true/false)")
        configs.append(
            ProviderConfig(
                name=item["name"].strip(),
                protocol=protocol,
                model=item["model"].strip(),
                base_url=item["base_url"].strip().rstrip("/"),
                api_key=item["api_key"].strip(),
                thinking=thinking,
            )
        )
    return configs


def select_profile(configs: list[ProviderConfig], name: str | None = None) -> ProviderConfig:
    """按 name 选择配置；name 为空取第一份；找不到抛 ConfigError。"""
    if not configs:
        raise ConfigError("没有可用配置")
    if name is None:
        return configs[0]
    for config in configs:
        if config.name == name:
            return config
    available = ", ".join(c.name for c in configs)
    raise ConfigError(f"找不到名为 {name!r} 的配置，可用配置: {available}")


def load_custom_instructions(path: str | os.PathLike) -> str:
    """读取 YAML 顶层可选字段 custom_instructions（多行文本），拼进系统提示的
    「自定义指令」模块。

    文件缺失、字段缺失或字段非字符串一律返回空串（该模块不参与拼装）；
    文件本身损坏抛 ConfigError（与 load_config 的校验口径一致）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return ""
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件格式错误: {e}")
    if not isinstance(data, dict):
        return ""
    value = data.get("custom_instructions", "")
    return value.strip() if isinstance(value, str) else ""
