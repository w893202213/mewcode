"""统一 agent 工具测试（ch16 F2）：参数校验、同步/后台分流、父上下文。"""

import time
from pathlib import Path

from mewcode.agent.loop import Agent
from mewcode.agents.context import ParentContext, clear, push
from mewcode.agents.manager import TaskManager, TaskStatus
from mewcode.agents.model import AgentRole
from mewcode.config import ProviderConfig
from mewcode.provider.base import Done, TextDelta as PTextDelta
from mewcode.session import ChatSession
from mewcode.tools import create_registry
from mewcode.tools.agent import AgentTool


class FakeProvider:
    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.systems = []

    def stream(self, messages, tools, system):
        self.calls += 1
        self.systems.append(system)
        return iter(self._handler(self.calls, messages, tools))


def _role(name="reviewer", tools=None):
    return AgentRole(
        name=name,
        description=f"角色 {name}",
        body="角色正文",
        tools=tools,
        deny_tools=(),
        model=None,
        max_rounds=30,
        permission=None,
        source=Path(f"{name}.md"),
        level="test",
    )


def _cfg() -> ProviderConfig:
    return ProviderConfig(
        name="main", protocol="openai", model="deepseek-flash",
        base_url="https://x.test", api_key="sk-test",
    )


def _env(tmp_path, handler):
    """构造 TaskManager + 主 Agent + 工具，并 push 父上下文。"""
    provider = FakeProvider(handler)
    registry = create_registry()
    parent = Agent(provider, registry, ChatSession())
    manager = TaskManager(
        roles={"reviewer": _role()},
        providers=[_cfg()],
        default_config=_cfg(),
        default_provider=provider,
        registry=registry,
        hook_manager=None,
        file_rules=[],
        project_root=tmp_path,
    )
    manager._default_provider = provider  # 子 Agent 用同一 FakeProvider
    tool = AgentTool()
    push(ParentContext(agent=parent, task_manager=manager, read_cache=None))
    return manager, tool


def _call(tool, **args):
    try:
        return tool.execute(dict(args))
    finally:
        clear()


# ----------------------------------------------------------------------
# 参数校验（无上下文也能报出参数错误——校验先于上下文）
# ----------------------------------------------------------------------


def test_invalid_type():
    clear()
    tool = AgentTool()
    result = tool.execute({"type": "spawn", "prompt": "x"})
    assert result.ok is False
    assert "defined/fork" in result.content


def test_missing_prompt():
    clear()
    tool = AgentTool()
    result = tool.execute({"type": "defined", "role": "reviewer"})
    assert result.ok is False
    assert "prompt" in result.content


def test_defined_requires_role():
    clear()
    tool = AgentTool()
    result = tool.execute({"type": "defined", "prompt": "x"})
    assert result.ok is False
    assert "role" in result.content


def test_role_not_found_lists_names(tmp_path):
    def handler(n, messages, tools):
        return [PTextDelta("好"), Done()]

    manager, tool = _env(tmp_path, handler)
    result = _call(tool, type="defined", role="missing", prompt="任务")
    assert result.ok is False
    assert "reviewer" in result.content  # 可用角色名


def test_no_parent_context_fails():
    clear()
    tool = AgentTool()
    result = tool.execute({"type": "defined", "role": "reviewer", "prompt": "x"})
    assert result.ok is False
    assert "仅主对话可用" in result.content


# ----------------------------------------------------------------------
# 同步与后台分流（F2/F8）
# ----------------------------------------------------------------------


def test_sync_returns_final_reply(tmp_path):
    def handler(n, messages, tools):
        return [PTextDelta("审查结论：通过"), Done()]

    manager, tool = _env(tmp_path, handler)
    result = _call(tool, type="defined", role="reviewer", prompt="审查")
    assert result.ok is True
    assert result.content == "审查结论：通过"
    tasks = manager.snapshot()
    assert len(tasks) == 1
    assert tasks[0].status is TaskStatus.DONE
    assert tasks[0].background is False


def test_background_returns_task_id(tmp_path):
    def handler(n, messages, tools):
        time.sleep(1.5)
        return [PTextDelta("慢结果"), Done()]

    manager, tool = _env(tmp_path, handler)
    result = _call(tool, type="defined", role="reviewer", prompt="任务", background=True)
    assert result.ok is True
    assert "任务 #1" in result.content
    assert "后台" in result.content
    # 立即返回不等待
    assert manager.snapshot()[0].status is TaskStatus.RUNNING
    manager.snapshot()[0].done_event.wait(5)
    time.sleep(0.1)
    assert manager.snapshot()[0].status is TaskStatus.DONE


def test_fork_forces_background(tmp_path):
    def handler(n, messages, tools):
        return [PTextDelta("好"), Done()]

    manager, tool = _env(tmp_path, handler)
    result = _call(tool, type="fork", prompt="深入排查", background=False)
    assert result.ok is True
    assert "后台" in result.content  # 显式 false 仍进后台
    task = manager.snapshot()[0]
    assert task.background is True
    assert task.kind == "fork"


def test_schema_metadata():
    tool = AgentTool()
    assert tool.name == "agent"
    assert tool.read_only is False
    assert tool.input_schema["required"] == ["type", "prompt"]
    assert tool.input_schema["properties"]["type"]["enum"] == ["defined", "fork"]
