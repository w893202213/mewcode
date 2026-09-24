"""Hook subagent 动作对接测试（ch16 F10）：真实启动、role 缺省、无上下文占位。"""

import asyncio
import logging
from pathlib import Path

import pytest

from mewcode.agent.loop import Agent
from mewcode.agents.context import ParentContext, clear, push
from mewcode.agents.manager import TaskManager, TaskStatus
from mewcode.agents.model import AgentRole
from mewcode.config import ConfigError
from mewcode.hooks.actions import exec_subagent
from mewcode.hooks.loader import HookAction, parse_hooks
from mewcode.provider.base import Done, TextDelta as PTextDelta
from mewcode.session import ChatSession
from mewcode.tools import create_registry


class FakeProvider:
    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.systems = []

    def stream(self, messages, tools, system):
        self.calls += 1
        self.systems.append(system)
        return iter(self._handler(self.calls, messages, tools))


def _role(name="general"):
    return AgentRole(
        name=name,
        description=f"角色 {name}",
        body="正文",
        tools=None,
        deny_tools=(),
        model=None,
        max_rounds=30,
        permission=None,
        source=Path(f"{name}.md"),
        level="test",
    )


def _cfg():
    from mewcode.config import ProviderConfig

    return ProviderConfig(
        name="main", protocol="openai", model="deepseek-flash",
        base_url="https://x.test", api_key="sk-test",
    )


def _env(tmp_path, handler):
    provider = FakeProvider(handler)
    registry = create_registry()
    parent = Agent(provider, registry, ChatSession())
    manager = TaskManager(
        roles={"general": _role("general"), "custom": _role("custom")},
        providers=[_cfg()],
        default_config=_cfg(),
        default_provider=provider,
        registry=registry,
        hook_manager=None,
        file_rules=[],
        project_root=tmp_path,
    )
    manager._default_provider = provider
    return manager, parent


# ----------------------------------------------------------------------
# 真实启动（F10）
# ----------------------------------------------------------------------


def test_exec_subagent_starts_task(tmp_path):
    def handler(n, messages, tools):
        return [PTextDelta("完成"), Done()]

    manager, parent = _env(tmp_path, handler)
    action = HookAction(type="subagent", prompt="检查代码")
    push(ParentContext(agent=parent, task_manager=manager, read_cache=None))
    try:
        result = exec_subagent(action)
    finally:
        clear()
    assert result.ok is True
    assert "任务 #1" in result.message
    task = manager.snapshot()[0]
    assert task.done_event.wait(5)
    assert task.status is TaskStatus.DONE
    assert task.role == "general"  # 缺省 general


def test_exec_subagent_uses_specified_role(tmp_path):
    def handler(n, messages, tools):
        return [PTextDelta("完成"), Done()]

    manager, parent = _env(tmp_path, handler)
    action = HookAction(type="subagent", prompt="检查代码", role="custom")
    push(ParentContext(agent=parent, task_manager=manager, read_cache=None))
    try:
        result = exec_subagent(action)
    finally:
        clear()
    assert result.ok is True
    assert manager.snapshot()[0].role == "custom"


def test_exec_subagent_via_to_thread_sees_context(tmp_path):
    """生产路径回归：Hook 动作经 asyncio.to_thread 执行（manager 内部调度），
    contextvars 随线程复制——subagent 动作仍能拿到 TaskManager。

    修复背景：threading.local 不跨线程，该路径曾永远退回占位
    （单测因主线程直接 push 而误绿，e2e 审查发现）。
    """

    def handler(n, messages, tools):
        return [PTextDelta("完成"), Done()]

    manager, parent = _env(tmp_path, handler)
    push(ParentContext(agent=parent, task_manager=manager, read_cache=None))
    try:
        result = asyncio.run(
            asyncio.to_thread(
                exec_subagent, HookAction(type="subagent", prompt="检查代码")
            )
        )
    finally:
        clear()
    assert result.ok is True
    assert "任务 #1" in result.message
    assert manager.snapshot()[0].role == "general"


def test_exec_subagent_without_context_falls_back(caplog):
    clear()
    action = HookAction(type="subagent", prompt="检查代码")
    with caplog.at_level(logging.INFO, logger="mewcode.hooks"):
        result = exec_subagent(action)
    assert result.ok is True
    assert "占位" in caplog.text


# ----------------------------------------------------------------------
# loader 校验（F10）
# ----------------------------------------------------------------------


def test_loader_accepts_subagent_role():
    rules = parse_hooks(
        {
            "hooks": [
                {
                    "event": "tool_after",
                    "action": {"type": "subagent", "prompt": "检查", "role": "custom"},
                }
            ]
        }
    )
    assert rules[0].action.role == "custom"


def test_loader_subagent_role_defaults_empty():
    rules = parse_hooks(
        {
            "hooks": [
                {
                    "event": "tool_after",
                    "action": {"type": "subagent", "prompt": "检查"},
                }
            ]
        }
    )
    assert rules[0].action.role == ""


def test_loader_rejects_non_string_role():
    with pytest.raises(ConfigError, match="role"):
        parse_hooks(
            {
                "hooks": [
                    {
                        "event": "tool_after",
                        "action": {"type": "subagent", "prompt": "检查", "role": 3},
                    }
                ]
            }
        )
