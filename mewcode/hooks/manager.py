"""Hook 管理器：事件分组索引、emit 流程、run_once、background 调度、
注入队列与失败日志（ch15 F4/F6/F7）。

emit（同步，装配层用）与 emit_async（异步，主循环内用）共用同一核心
流程：过滤该事件规则 → 逐条条件求值 → 命中执行动作 → 汇总决策。
拦截类事件（tool_before）上动作 deny 或失败产生拒绝（fail-closed）；
其余事件只记日志、绝不中断 Agent 主流程。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass

from mewcode.hooks.actions import (
    ActionResult,
    exec_command,
    exec_http,
    exec_inject,
    exec_subagent,
)
from mewcode.hooks.loader import INTERCEPT_EVENTS, HookRule
from mewcode.hooks.matcher import eval_conditions

logger = logging.getLogger("mewcode.hooks")


@dataclass
class HookDecision:
    """一次 emit 的决策：仅拦截类事件可能 allowed=False。"""

    allowed: bool = True
    reason: str = ""  # 拒绝原因（回灌给模型）


class HookManager:
    """规则集合的运行时：分组索引 + 双版本 emit + 注入队列。"""

    def __init__(self, rules: list[HookRule]) -> None:
        self._by_event: dict[str, list[HookRule]] = {}
        for rule in rules:
            self._by_event.setdefault(rule.event, []).append(rule)
        self._executed_once: set[int] = set()  # run_once 已执行规则序号
        self._pending_injects: list[str] = []  # 待注入提示词（Agent 每轮取走）

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def emit(self, event: str, ctx: dict) -> HookDecision:
        """同步触发（装配层：startup/shutdown/session 级）。

        background 动作走 daemon 线程不阻塞；拦截类事件强制同步等待结果。
        """
        rules = self._by_event.get(event)
        if not rules:
            return HookDecision()  # 该事件无规则：零开销
        for rule in rules:
            if not self._pass(rule, ctx):
                continue
            if rule.background and rule.action.type != "inject":
                # inject 是即时入队操作，无论 background 与否都同步执行
                threading.Thread(
                    target=self._run_action_bg,
                    args=(rule, ctx),
                    daemon=True,
                    name=f"hook-bg-{rule.index}",
                ).start()
                continue
            try:
                result = self._run_action(rule, ctx)
            except Exception as e:  # 动作实现异常也绝不中断主流程（F7）
                logger.warning(
                    "Hook 动作异常: 事件 %s 规则 #%d: %s", event, rule.index, e
                )
                if rule.event in INTERCEPT_EVENTS:
                    return HookDecision(allowed=False, reason=f"Hook 动作异常: {e}")
                continue
            decision = self._apply_result(rule, result)
            if decision is not None:
                return decision
        return HookDecision()

    async def emit_async(self, event: str, ctx: dict) -> HookDecision:
        """异步触发（主循环内：轮次/消息/工具级）。

        同步动作经 asyncio.to_thread 执行，不阻塞事件循环；background
        动作走 asyncio.create_task。
        """
        rules = self._by_event.get(event)
        if not rules:
            return HookDecision()
        for rule in rules:
            if not self._pass(rule, ctx):
                continue
            if rule.background and rule.action.type != "inject":
                # background 统一走 daemon 线程：短命事件循环退出时不
                # 被 pending task 拖住（与同步版一致）
                threading.Thread(
                    target=self._run_action_bg,
                    args=(rule, ctx),
                    daemon=True,
                    name=f"hook-bg-{rule.index}",
                ).start()
                continue
            try:
                result = await asyncio.to_thread(self._run_action, rule, ctx)
            except Exception as e:
                logger.warning(
                    "Hook 动作异常: 事件 %s 规则 #%d: %s", event, rule.index, e
                )
                if rule.event in INTERCEPT_EVENTS:
                    return HookDecision(allowed=False, reason=f"Hook 动作异常: {e}")
                continue
            decision = self._apply_result(rule, result)
            if decision is not None:
                return decision
        return HookDecision()

    def take_injects(self) -> list[str]:
        """取走全部待注入提示词（全取全清：下一次请求发出即移除）。"""
        pending = self._pending_injects
        self._pending_injects = []
        return pending

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _pass(self, rule: HookRule, ctx: dict) -> bool:
        """前置过滤：命中返回 True；run_once 已执行/条件不命中返回 False。"""
        if rule.run_once and rule.index in self._executed_once:
            return False
        if not eval_conditions(rule.conditions, rule.match, ctx):
            logger.debug(
                "Hook 跳过: 事件 %s 规则 #%d 条件不命中", rule.event, rule.index
            )
            return False
        if rule.run_once:
            self._executed_once.add(rule.index)
        logger.info("Hook 触发: 事件 %s 规则 #%d", rule.event, rule.index)
        return True

    def _run_action(self, rule: HookRule, ctx: dict) -> ActionResult:
        """按动作类型分发执行；inject 的文本入队。"""
        action = rule.action
        if action.type == "command":
            return exec_command(action, ctx, rule.timeout)
        if action.type == "http":
            return exec_http(action, rule.timeout)
        if action.type == "inject":
            result = exec_inject(action)
            if result.message:
                self._pending_injects.append(result.message)
            return result
        return exec_subagent(action)

    def _apply_result(
        self, rule: HookRule, result: ActionResult
    ) -> HookDecision | None:
        """同步动作结果处理：拦截事件 deny/失败→拒绝决策（短路）；其余记日志。"""
        if rule.event in INTERCEPT_EVENTS:
            if result.deny:
                return HookDecision(allowed=False, reason=result.message)
            if not result.ok:
                # 拦截动作自身失败：fail-closed，拒绝该调用（F4）
                return HookDecision(allowed=False, reason=result.message)
        if not result.ok:
            logger.warning(
                "Hook 动作失败: 事件 %s 规则 #%d: %s",
                rule.event,
                rule.index,
                result.message,
            )
        elif result.deny:
            logger.warning(
                "Hook 拒绝语义仅在拦截事件生效: 事件 %s 规则 #%d: %s",
                rule.event,
                rule.index,
                result.message,
            )
        return None

    def _run_action_bg(self, rule: HookRule, ctx: dict) -> None:
        """后台动作：线程/任务内执行，失败只记日志、不参与决策。"""
        try:
            result = self._run_action(rule, ctx)
        except Exception as e:
            logger.warning(
                "Hook 后台动作异常: 事件 %s 规则 #%d: %s",
                rule.event,
                rule.index,
                e,
            )
            return
        if not result.ok:
            logger.warning(
                "Hook 后台动作失败: 事件 %s 规则 #%d: %s",
                rule.event,
                rule.index,
                result.message,
            )
        elif result.deny:
            logger.warning(
                "Hook 后台动作的拒绝语义被忽略: 事件 %s 规则 #%d",
                rule.event,
                rule.index,
            )
