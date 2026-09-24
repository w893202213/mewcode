"""Hook 管理器测试（ch15 F4/F6/F7）：分组、顺序、run_once、background、
拦截决策、失败隔离、注入队列。"""

import asyncio
import logging
import time

from mewcode.hooks.loader import HookAction, HookCondition, HookRule
from mewcode.hooks.manager import HookDecision, HookManager


def _rule(
    event: str,
    action: HookAction,
    conditions: list | None = None,
    match: str = "all",
    run_once: bool = False,
    background: bool = False,
    timeout: float = 10.0,
    index: int = 1,
) -> HookRule:
    return HookRule(
        index=index,
        event=event,
        conditions=conditions or [],
        match=match,
        action=action,
        run_once=run_once,
        background=background,
        timeout=timeout,
    )


def _cmd(run: str) -> HookAction:
    return HookAction(type="command", run=run)


CTX = {"event": "tool_before", "round": 1, "tool": "run_command", "args": {}}


# ----------------------------------------------------------------------
# 分组与触发
# ----------------------------------------------------------------------


def test_no_rules_allows():
    assert HookManager([]).emit("tool_before", CTX) == HookDecision()


def test_event_without_rules_allows_and_no_action(tmp_path):
    mark = tmp_path / "mark"
    manager = HookManager(
        [_rule("startup", _cmd(f"touch {mark}"), index=1)]
    )
    decision = manager.emit("round_start", {"event": "round_start"})
    assert decision.allowed is True
    assert not mark.exists()  # 该事件无规则：动作不执行


def test_unconditional_rule_triggers(tmp_path):
    mark = tmp_path / "mark"
    manager = HookManager([_rule("round_start", _cmd(f"touch {mark}"))])
    manager.emit("round_start", {"event": "round_start"})
    assert mark.exists()


def test_condition_miss_skips(tmp_path, caplog):
    mark = tmp_path / "mark"
    rule = _rule(
        "tool_before",
        _cmd(f"touch {mark}"),
        conditions=[HookCondition("args.command", "rm *")],
    )
    manager = HookManager([rule])
    with caplog.at_level(logging.DEBUG, logger="mewcode.hooks"):
        decision = manager.emit("tool_before", CTX)
    assert decision.allowed is True
    assert not mark.exists()
    assert "条件不命中" in caplog.text


def test_multiple_rules_all_run_in_order(tmp_path):
    log = tmp_path / "log"
    manager = HookManager(
        [
            _rule("round_start", _cmd(f"echo a >> {log}"), index=1),
            _rule("round_start", _cmd(f"echo b >> {log}"), index=2),
        ]
    )
    manager.emit("round_start", {"event": "round_start"})
    assert log.read_text().strip() == "a\nb"


# ----------------------------------------------------------------------
# run_once / background
# ----------------------------------------------------------------------


def test_run_once_executes_once(tmp_path):
    log = tmp_path / "log"
    manager = HookManager(
        [_rule("round_start", _cmd(f"echo x >> {log}"), run_once=True)]
    )
    manager.emit("round_start", {"event": "round_start"})
    manager.emit("round_start", {"event": "round_start"})
    assert log.read_text().strip() == "x"


def test_background_does_not_block_sync(tmp_path):
    mark = tmp_path / "mark"
    manager = HookManager(
        [_rule("round_start", _cmd(f"sleep 1; touch {mark}"), background=True)]
    )
    start = time.monotonic()
    manager.emit("round_start", {"event": "round_start"})
    assert time.monotonic() - start < 0.5  # 立即返回
    time.sleep(1.3)
    assert mark.exists()  # 后台动作最终完成


def test_background_does_not_block_async(tmp_path):
    mark = tmp_path / "mark"
    manager = HookManager(
        [_rule("round_start", _cmd(f"sleep 1; touch {mark}"), background=True)]
    )
    start = time.monotonic()
    asyncio.run(manager.emit_async("round_start", {"event": "round_start"}))
    assert time.monotonic() - start < 0.5
    time.sleep(1.3)
    assert mark.exists()


# ----------------------------------------------------------------------
# 拦截决策（tool_before）
# ----------------------------------------------------------------------


def test_intercept_deny_rejects_with_stdout():
    manager = HookManager([_rule("tool_before", _cmd("echo 危险参数; exit 1"))])
    decision = manager.emit("tool_before", CTX)
    assert decision.allowed is False
    assert decision.reason == "危险参数"


def test_intercept_action_failure_fails_closed():
    # 超时 = 动作失败 → fail-closed 拒绝（F4）
    manager = HookManager(
        [_rule("tool_before", _cmd("sleep 5"), timeout=1)]
    )
    decision = manager.emit("tool_before", CTX)
    assert decision.allowed is False
    assert "超时" in decision.reason


def test_intercept_deny_short_circuits_later_rules(tmp_path):
    mark = tmp_path / "mark"
    manager = HookManager(
        [
            _rule("tool_before", _cmd("exit 1"), index=1),
            _rule("tool_before", _cmd(f"touch {mark}"), index=2),
        ]
    )
    decision = manager.emit("tool_before", CTX)
    assert decision.allowed is False
    assert not mark.exists()  # 第一条拒绝后短路，后续规则不执行


def test_async_intercept_deny():
    manager = HookManager([_rule("tool_before", _cmd("exit 1"))])
    decision = asyncio.run(manager.emit_async("tool_before", CTX))
    assert decision.allowed is False


# ----------------------------------------------------------------------
# 失败隔离（非拦截事件）
# ----------------------------------------------------------------------


def test_non_intercept_failure_only_logs(tmp_path, caplog):
    mark = tmp_path / "mark"
    manager = HookManager(
        [
            _rule("round_start", _cmd("exit 1"), index=1),
            _rule("round_start", _cmd(f"touch {mark}"), index=2),
        ]
    )
    with caplog.at_level(logging.WARNING, logger="mewcode.hooks"):
        decision = manager.emit("round_start", {"event": "round_start"})
    assert decision.allowed is True  # 主流程不受影响
    assert mark.exists()  # 后续规则照常执行
    # exit 1 的命令 ok=False：非拦截事件只记「动作失败」警告
    assert "Hook 动作失败: 事件 round_start 规则 #1" in caplog.text


# ----------------------------------------------------------------------
# 注入队列与日志
# ----------------------------------------------------------------------


def test_inject_queue_take_and_clear():
    manager = HookManager(
        [
            _rule(
                "tool_after",
                HookAction(type="inject", prompt="注意安全"),
            )
        ]
    )
    manager.emit("tool_after", {"event": "tool_after", "tool": "run_command"})
    assert manager.take_injects() == ["注意安全"]
    assert manager.take_injects() == []


def test_trigger_log_has_event_and_index(caplog):
    manager = HookManager([_rule("round_start", _cmd("true"), index=3)])
    with caplog.at_level(logging.INFO, logger="mewcode.hooks"):
        manager.emit("round_start", {"event": "round_start"})
    assert "Hook 触发: 事件 round_start 规则 #3" in caplog.text


# ----------------------------------------------------------------------
# 引擎级异常兜底（F7）
# ----------------------------------------------------------------------


def _boom(conditions, match, ctx):
    raise RuntimeError("boom")


def test_engine_error_non_intercept_logs_and_allows(monkeypatch, caplog):
    monkeypatch.setattr("mewcode.hooks.manager.eval_conditions", _boom)
    manager = HookManager([_rule("round_start", _cmd("true"))])
    with caplog.at_level(logging.WARNING, logger="mewcode.hooks"):
        decision = manager.emit("round_start", {"event": "round_start"})
    assert decision.allowed is True  # 绝不中断主流程
    assert "Hook 引擎异常" in caplog.text


def test_engine_error_intercept_fails_closed(monkeypatch):
    monkeypatch.setattr("mewcode.hooks.manager.eval_conditions", _boom)
    manager = HookManager([_rule("tool_before", _cmd("true"))])
    decision = manager.emit("tool_before", CTX)
    assert decision.allowed is False
    assert "boom" in decision.reason
