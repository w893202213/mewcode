"""Anthropic Provider：Claude Messages API 流式调用与事件解析。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from mewcode.config import ProviderConfig
from mewcode.provider.base import (
    Done,
    Provider,
    ProviderError,
    StreamEvent,
    TextDelta,
    ThinkingDelta,
)
from mewcode.session import Message

# 内置常量（配置中无对应字段）
DEFAULT_MAX_TOKENS = 8192
THINKING_BUDGET = 4096  # 官方下限 1024，且必须小于 max_tokens
API_VERSION = "2023-06-01"


def _build_payload(config: ProviderConfig, messages: list[Message]) -> dict[str, Any]:
    """把对话历史转成 Messages API 请求体。"""
    body_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            body_messages.append({"role": "user", "content": m.text})
        else:
            # assistant 消息按 content 数组组装；思考块必须原样回传（官方硬性要求）
            content: list[dict[str, Any]] = [dict(b) for b in m.thinking_raw]
            content.append({"type": "text", "text": m.text})
            body_messages.append({"role": "assistant", "content": content})
    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": True,
        "messages": body_messages,
    }
    if config.thinking:
        payload["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}
    # 注意：不设置 temperature（与 extended thinking 互斥）
    return payload


def _iter_data_objects(lines: Iterator[str]) -> Iterator[dict]:
    """逐行解析 SSE 流，产出每个 `data:` 行的 JSON 对象。"""
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue  # 容忍无法解析的行，不中断流


def _http_error_message(resp: httpx.Response) -> str:
    """把非 200 响应转成友好中文错误信息（绝不回显 api_key）。"""
    detail = ""
    try:
        body = resp.json()
        detail = (body.get("error") or {}).get("message", "") or body.get("message", "")
    except Exception:
        pass
    hints = {
        401: "鉴权失败：api_key 无效或无权限",
        403: "禁止访问：api_key 无此操作权限",
        404: "接口不存在：请检查 base_url 是否正确",
        429: "请求过于频繁或额度不足（限流）",
    }
    reason = hints.get(resp.status_code, "请求失败")
    if detail:
        return f"{reason}（HTTP {resp.status_code}）: {detail}"
    return f"{reason}（HTTP {resp.status_code}）"


class AnthropicProvider(Provider):
    """Claude Messages API 流式后端。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    def stream(self, messages: list[Message]) -> Iterator[StreamEvent]:
        """发起流式请求，逐个产出归一化事件；失败抛 ProviderError。"""
        url = f"{self._config.base_url}/v1/messages"
        headers = {
            "x-api-key": self._config.api_key,
            "anthropic-version": API_VERSION,
            "content-type": "application/json",
        }
        # 注意：read 超时置空——长回复生成期间不能因读超时中断
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST", url, headers=headers, json=_build_payload(self._config, messages)
                ) as resp:
                    if resp.status_code != 200:
                        raise ProviderError(_http_error_message(resp))
                    yield from self._iter_events(_iter_data_objects(resp.iter_lines()))
        except httpx.HTTPError as e:
            raise ProviderError(f"网络错误，无法连接 {self._config.base_url}: {e}") from e

    def _iter_events(self, objects: Iterator[dict]) -> Iterator[StreamEvent]:
        """把已解析的事件对象分派成归一化流事件（纯逻辑，可脱离网络测试）。

        同时收集思考块的原始内容（含签名），流结束时随 Done 交给调用方回存。
        """
        current_thinking: list[str] | None = None  # None = 当前不在思考块内
        thinking_signature = ""
        thinking_blocks: list[dict] = []

        for obj in objects:
            obj_type = obj.get("type")
            if obj_type == "content_block_start":
                block = obj.get("content_block") or {}
                if block.get("type") == "thinking":
                    current_thinking = []
                    thinking_signature = block.get("signature", "")
            elif obj_type == "content_block_delta":
                delta = obj.get("delta") or {}
                if delta.get("type") == "thinking_delta":
                    piece = delta.get("thinking", "")
                    if current_thinking is not None:
                        current_thinking.append(piece)
                        # 签名随 delta 增量累积，最后一个 thinking_delta 携带最终签名
                        if delta.get("signature"):
                            thinking_signature = delta["signature"]
                    yield ThinkingDelta(piece)
                elif delta.get("type") == "text_delta":
                    yield TextDelta(delta.get("text", ""))
            elif obj_type == "content_block_stop":
                if current_thinking is not None:
                    thinking_blocks.append(
                        {
                            "type": "thinking",
                            "thinking": "".join(current_thinking),
                            "signature": thinking_signature,
                        }
                    )
                    current_thinking = None
            elif obj_type == "message_stop":
                yield Done(thinking_raw=thinking_blocks)
                return
            elif obj_type == "error":
                error = obj.get("error") or {}
                raise ProviderError(f"服务端返回错误: {error.get('message', obj)}")
