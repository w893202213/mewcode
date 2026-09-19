"""Provider 层：统一 LLM 后端抽象。

新增后端 = 实现 Provider 子类 + 在 _REGISTRY 加一行，其余代码零改动。
"""

from __future__ import annotations

from mewcode.config import ProviderConfig
from mewcode.provider.anthropic import AnthropicProvider
from mewcode.provider.base import Provider, ProviderError
from mewcode.provider.openai import OpenAIProvider

# 协议名 → Provider 类
_REGISTRY: dict[str, type[Provider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def create_provider(config: ProviderConfig) -> Provider:
    """按配置的 protocol 字段创建对应后端实例。"""
    cls = _REGISTRY.get(config.protocol)
    if cls is None:
        raise ProviderError(
            f"不支持的协议: {config.protocol!r}（可选: {', '.join(sorted(_REGISTRY))}）"
        )
    return cls(config)


__all__ = ["Provider", "ProviderError", "StreamEvent", "create_provider"]
