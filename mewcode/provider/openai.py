"""OpenAI Provider：chat/completions 流式调用与事件解析（兼容服务同样适用）。"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import httpx

from mewcode.config import ProviderConfig
from mewcode.provider.base import Done, Provider, ProviderError, StreamEvent, TextDelta
from mewcode.session import Message


def _build_payload(config: ProviderConfig, messages: list[Message]) -> dict[str, Any]:
    """把对话历史转成 chat/completions 请求体。

    只携带 role + content 文本；thinking/thinking_raw 不回传（OpenAI 协议无此机制）。
    """
    body_messages = [{"role": m.role, "content": m.text} for m in messages]
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": body_messages,
        "stream": True,
    }
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

    def stream(self, messages: list[Message]) -> Iterator[StreamEvent]:
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
                    "POST", url, headers=headers, json=_build_payload(self._config, messages)
                ) as resp:
                    if resp.status_code != 200:
                        raise ProviderError(_http_error_message(resp))
                    yield from self._iter_events(_iter_data_objects(resp.iter_lines()))
        except httpx.HTTPError as e:
            raise ProviderError(f"网络错误，无法连接 {self._config.base_url}: {e}") from e

    def _iter_events(self, objects: Iterator[dict]) -> Iterator[StreamEvent]:
        """把已解析的事件对象分派成归一化流事件（纯逻辑，可脱离网络测试）。"""
        for obj in objects:
            # 错误对象优先处理
            error = obj.get("error")
            if error:
                message = error.get("message", error) if isinstance(error, dict) else error
                raise ProviderError(f"服务端返回错误: {message}")
            choices = obj.get("choices") or []
            if not choices:
                continue
            choice = choices[0]
            # 增量文本（reasoning_content 本步不解析，见 plan 技术决策）
            content = (choice.get("delta") or {}).get("content")
            if content:
                yield TextDelta(content)
            if choice.get("finish_reason"):
                yield Done()
                return
