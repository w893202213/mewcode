"""Hook 动作执行：四种动作的同步执行函数（ch15 F5）。

全部阻塞函数，由 manager 决定放线程/任务（background 调度差异在
manager 层）；command/http 受 timeout 约束（F6）。ActionResult 统一
表达「动作自身成败 + 拦截拒绝语义」：deny=True 表示非零退出 / 非 2xx，
仅拦截类事件（tool_before）上产生拒绝，其余事件只记日志。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass

import httpx

from mewcode.hooks.loader import HookAction

logger = logging.getLogger("mewcode.hooks")

# 拒绝原因/动作消息的截断上限（防超长 stdout/响应体撑爆回灌内容）
TRUNCATE_LIMIT = 1000


@dataclass
class ActionResult:
    """一次动作执行的结果。"""

    ok: bool  # 动作自身成功与否
    message: str  # stdout / 响应摘要 / 错误信息（截断后）
    deny: bool = False  # 非零退出 / 非 2xx → 拦截类事件的拒绝语义


def _truncate(text: str, limit: int = TRUNCATE_LIMIT) -> str:
    """超长文本截断（strip 后保留前 limit 字符）。"""
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit]


def exec_inject(action: HookAction) -> ActionResult:
    """注入提示词：恒成功，message 即注入文本（manager 入队）。"""
    return ActionResult(ok=True, message=action.prompt)


def exec_subagent(action: HookAction) -> ActionResult:
    """子 Agent：占位动作（ch15 不做真实运行），日志后返回成功。"""
    logger.info("Hook 子 Agent 动作占位，暂不执行（待 SubAgent 章节对接）")
    return ActionResult(ok=True, message="子 Agent 动作占位")


def exec_command(action: HookAction, ctx: dict, timeout: float) -> ActionResult:
    """执行 shell 命令：完整事件上下文 JSON 经 stdin 传入，常用字段暴露为
    环境变量（MEW_EVENT / MEW_TOOL）。退出码 0→成功；非 0→deny（stdout
    为拒绝原因，为空时退化为 stderr/退出码）；超时→失败（fail-closed）。
    """
    env = os.environ.copy()
    env["MEW_EVENT"] = str(ctx.get("event", ""))
    if "tool" in ctx:
        env["MEW_TOOL"] = str(ctx["tool"])
    try:
        proc = subprocess.run(
            action.run,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            input=json.dumps(ctx, ensure_ascii=False),
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ActionResult(ok=False, message=f"命令超时（{timeout} 秒）")
    if proc.returncode == 0:
        return ActionResult(ok=True, message=_truncate(proc.stdout))
    reason = _truncate(proc.stdout or proc.stderr or f"命令退出码 {proc.returncode}")
    return ActionResult(ok=False, message=reason, deny=True)


def exec_http(
    action: HookAction, timeout: float, client: httpx.Client | None = None
) -> ActionResult:
    """发 HTTP 请求：2xx→成功；非 2xx→deny（拦截语义按状态码，响应体截断
    为原因）；网络异常/超时→失败（fail-closed）。

    client 参数供测试注入 MockTransport（缺省自建带超时的 Client）。
    """
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout)
    try:
        resp = client.request(
            action.method,
            action.url,
            headers=action.headers or None,
            content=action.body.encode("utf-8") if action.body else None,
        )
    except httpx.HTTPError as e:
        if owns_client:
            client.close()
        return ActionResult(ok=False, message=f"HTTP 请求失败: {e}")
    if owns_client:
        client.close()
    if 200 <= resp.status_code < 300:
        return ActionResult(ok=True, message=f"HTTP {resp.status_code}")
    return ActionResult(ok=True, message=_truncate(resp.text), deny=True)
