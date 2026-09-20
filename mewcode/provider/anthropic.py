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
    ToolCall,
    UsageInfo,
)
from mewcode.session import Message
from mewcode.tools.base import Tool

# 内置常量（配置中无对应字段）
DEFAULT_MAX_TOKENS = 8192
THINKING_BUDGET = 4096  # 官方下限 1024，且必须小于 max_tokens
API_VERSION = "2023-06-01"


def _build_payload(
    config: ProviderConfig, messages: list[Message], tools: list[Tool]
) -> dict[str, Any]:
    """把对话历史与工具列表转成 Messages API 请求体。"""
    body_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "user":
            if m.tool_results:
                # 工具结果：content 为 tool_result 块列表（text 为空时不拼 text 块）
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": r.tool_use_id,
                        "content": r.content,
                        "is_error": r.is_error,
                    }
                    for r in m.tool_results
                ]
                body_messages.append({"role": "user", "content": content})
            else:
                body_messages.append({"role": "user", "content": m.text})
        else:
            # assistant 消息按 content 数组组装；思考块必须原样回传（官方硬性要求）
            content: list[dict[str, Any]] = [dict(b) for b in m.thinking_raw]
            for use in m.tool_uses:
                content.append(
                    {"type": "tool_use", "id": use.id, "name": use.name, "input": use.args}
                )
            content.append({"type": "text", "text": m.text})
            body_messages.append({"role": "assistant", "content": content})
    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "stream": True,
        "messages": body_messages,
    }
    if tools:
        payload["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
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

    def stream(self, messages: list[Message], tools: list[Tool]) -> Iterator[StreamEvent]:
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
                    "POST",
                    url,
                    headers=headers,
                    json=_build_payload(self._config, messages, tools),
                ) as resp:
                    if resp.status_code != 200:
                        raise ProviderError(_http_error_message(resp))
                    yield from self._iter_events(_iter_data_objects(resp.iter_lines()))
        except httpx.HTTPError as e:
            raise ProviderError(f"网络错误，无法连接 {self._config.base_url}: {e}") from e

    def _iter_events(self, objects: Iterator[dict]) -> Iterator[StreamEvent]:
        """把已解析的事件对象分派成归一化流事件（纯逻辑，可脱离网络测试）。

        同时收集思考块的原始内容（含签名），流结束时随 Done 交给调用方回存；
        工具调用（tool_use）的 input_json_delta 碎片在块内累积，块结束时
        拼接解析成 ToolCall 事件。
        """
        current_thinking: list[str] | None = None  # None = 当前不在思考块内
        thinking_signature = ""
        thinking_blocks: list[dict] = []

        # 工具调用块状态：None = 不在 tool_use 块内
        current_tool: dict[str, Any] | None = None  # {"id", "name", "fragments"}

        # token 用量：message_delta 携带，流结束随 Done 带出
        usage_in = 0
        usage_out = 0

        for obj in objects:
            obj_type = obj.get("type")
            if obj_type == "content_block_start":
                block = obj.get("content_block") or {}
                if block.get("type") == "thinking":
                    current_thinking = []
                    thinking_signature = block.get("signature", "")
                elif block.get("type") == "tool_use":
                    current_tool = {
                        "id": block.get("id", ""),
                        "name": block.get("name", ""),
                        "fragments": [],
                    }
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
                elif delta.get("type") == "input_json_delta":
                    # 工具参数 JSON 碎片：追加累积，块结束时统一解析
                    if current_tool is not None:
                        current_tool["fragments"].append(delta.get("partial_json", ""))
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
                if current_tool is not None:
                    yield from self._finish_tool_call(current_tool)
                    current_tool = None
            elif obj_type == "message_delta":
                usage = obj.get("usage") or {}
                usage_in = int(usage.get("input_tokens") or 0)
                usage_out = int(usage.get("output_tokens") or 0)
            elif obj_type == "message_stop":
                usage = (
                    UsageInfo(input_tokens=usage_in, output_tokens=usage_out)
                    if usage_in or usage_out
                    else None
                )
                yield Done(thinking_raw=thinking_blocks, usage=usage)
                return
            elif obj_type == "error":
                error = obj.get("error") or {}
                raise ProviderError(f"服务端返回错误: {error.get('message', obj)}")

    def _finish_tool_call(self, tool: dict[str, Any]) -> Iterator[StreamEvent]:
        """把累积完成的 tool_use 块解析成 ToolCall（参数解析失败时携带 parse_error）。"""
        raw = "".join(tool["fragments"])
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                raise ValueError("参数不是 JSON 对象")
            yield ToolCall(id=tool["id"], name=tool["name"], args=args)
        except (json.JSONDecodeError, ValueError) as e:
            snippet = raw[:100] + ("…" if len(raw) > 100 else "")
            yield ToolCall(
                id=tool["id"],
                name=tool["name"],
                args={},
                parse_error=f"工具参数 JSON 解析失败: {e}（原始片段: {snippet!r}）",
            )
