"""OpenAI Provider：chat/completions 流式调用与事件解析（兼容服务同样适用）。"""

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
    ToolCall,
    UsageInfo,
)
from mewcode.session import Message
from mewcode.tools.base import Tool


def _build_payload(
    config: ProviderConfig, messages: list[Message], tools: list[Tool]
) -> dict[str, Any]:
    """把对话历史与工具列表转成 chat/completions 请求体。

    只携带 role + content 文本；thinking/thinking_raw 不回传（OpenAI 协议无此机制）。
    工具调用回放为 assistant 的 tool_calls；工具结果回放为 tool 角色消息。
    """
    body_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant" and m.tool_uses:
            # 工具调用轮次：content 置空，tool_calls 按协议结构重建
            body_messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": use.id,
                            "type": "function",
                            "function": {
                                "name": use.name,
                                "arguments": json.dumps(use.args, ensure_ascii=False),
                            },
                        }
                        for use in m.tool_uses
                    ],
                }
            )
        elif m.tool_results:
            # 工具结果：每条记录一条 tool 角色消息
            for r in m.tool_results:
                body_messages.append(
                    {"role": "tool", "tool_call_id": r.tool_use_id, "content": r.content}
                )
        else:
            body_messages.append({"role": m.role, "content": m.text})
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": body_messages,
        "stream": True,
        # 让最终 chunk 携带 token 用量（F8）
        "stream_options": {"include_usage": True},
    }
    if tools:
        payload["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]
    if config.thinking:
        # 透传 thinking 参数，具体行为由服务端（含兼容服务）决定
        payload["thinking"] = True
    return payload


def _iter_data_objects(lines: Iterator[str]) -> Iterator[dict]:
    """逐行解析 SSE 流，产出每个 `data:` 行的 JSON 对象（`[DONE]` 行跳过）。"""
    for line in lines:
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
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
        err = body.get("error") or {}
        detail = err.get("message", "") if isinstance(err, dict) else ""
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


class OpenAIProvider(Provider):
    """OpenAI chat/completions 流式后端。"""

    def __init__(self, config: ProviderConfig) -> None:
        self._config = config

    def stream(self, messages: list[Message], tools: list[Tool]) -> Iterator[StreamEvent]:
        """发起流式请求，逐个产出归一化事件；失败抛 ProviderError。"""
        url = f"{self._config.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
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

        工具调用（tool_calls）按 index 聚合：首个碎片带 id/name，
        arguments 碎片串接，finish_reason="tool_calls" 时逐条解析成 ToolCall。
        """
        # index → {"id", "name", "arguments_fragments"}
        tool_calls: dict[int, dict[str, Any]] = {}

        # token 用量：include_usage 让最终 chunk 携带（可能附在 finish chunk 或独立的空 choices chunk）
        usage_in = 0
        usage_out = 0

        for obj in objects:
            # 错误对象优先处理
            error = obj.get("error")
            if error:
                message = error.get("message", error) if isinstance(error, dict) else error
                raise ProviderError(f"服务端返回错误: {message}")
            usage = obj.get("usage")
            if usage:
                usage_in = int(usage.get("prompt_tokens") or 0)
                usage_out = int(usage.get("completion_tokens") or 0)
            choices = obj.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            delta = choice.get("delta") or {}
            # 增量文本（reasoning_content 本步不解析，见 plan 技术决策）
            content = delta.get("content")
            if content:
                yield TextDelta(content)
            for piece in delta.get("tool_calls") or []:
                index = piece.get("index", 0)
                slot = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": []})
                if piece.get("id"):
                    slot["id"] = piece["id"]
                function = piece.get("function") or {}
                if function.get("name"):
                    slot["name"] = function["name"]
                if function.get("arguments"):
                    slot["arguments"].append(function["arguments"])
            usage_info = (
                UsageInfo(input_tokens=usage_in, output_tokens=usage_out)
                if usage_in or usage_out
                else None
            )
            if choice.get("finish_reason") == "tool_calls":
                # 工具调用轮次：逐条解析累积的参数碎片
                for index in sorted(tool_calls):
                    yield from self._finish_tool_call(tool_calls[index])
                yield Done(usage=usage_info)
                return
            if choice.get("finish_reason"):
                yield Done(usage=usage_info)
                return

    def _finish_tool_call(self, slot: dict[str, Any]) -> Iterator[StreamEvent]:
        """把聚合完成的 tool_calls 槽位解析成 ToolCall（参数解析失败携带 parse_error）。"""
        raw = "".join(slot["arguments"])
        try:
            args = json.loads(raw) if raw else {}
            if not isinstance(args, dict):
                raise ValueError("参数不是 JSON 对象")
            yield ToolCall(id=slot["id"], name=slot["name"], args=args)
        except (json.JSONDecodeError, ValueError) as e:
            snippet = raw[:100] + ("…" if len(raw) > 100 else "")
            yield ToolCall(
                id=slot["id"],
                name=slot["name"],
                args={},
                parse_error=f"工具参数 JSON 解析失败: {e}（原始片段: {snippet!r}）",
            )
