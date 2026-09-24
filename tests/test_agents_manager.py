"""SubAgent 任务管理器测试（ch16 F3/F4/F6/F7/F8）：构建、防线、同步等待、
后台转场、上限、token 回报。"""

import time
from pathlib import Path

from mewcode.agent.loop import Agent
from mewcode.agents.manager import AgentTask, TaskManager, TaskStatus
from mewcode.agents.model import AgentRole
from mewcode.config import ProviderConfig
from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.provider.base import Done, TextDelta as PTextDelta, ToolCall, UsageInfo
from mewcode.session import ChatSession
from mewcode.tools import create_registry
from mewcode.tools.base import Tool, ToolResult


class _FakeUseSkill(Tool):
    """防线测试用假 use_skill 工具（真实 use_skill 由装配层注册）。"""

    name = "use_skill"
    description = "假 use_skill"
    input_schema = {"type": "object", "properties": {}}

    def execute(self, args):
        return ToolResult.success("ok")


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0
        self.systems = []

    def stream(self, messages, tools, system):
        self.calls += 1
        self.systems.append(system)
        return iter(self._handler(self.calls, messages, tools))


def _role(name="reviewer", tools=None, deny_tools=(), model=None, permission=None,
          max_rounds=30, body="角色正文"):
    return AgentRole(
        name=name,
        description=f"角色 {name}",
        body=body,
        tools=tools,
        deny_tools=tuple(deny_tools),
        model=model,
        max_rounds=max_rounds,
        permission=permission,
        source=Path(f"{name}.md"),
        level="test",
    )


def _cfg(name="main", model="deepseek-flash") -> ProviderConfig:
    return ProviderConfig(
        name=name, protocol="openai", model=model,
        base_url="https://x.test", api_key="sk-test",
    )


def _manager(tmp_path, roles, provider_handler=None) -> tuple[TaskManager, FakeProvider, Agent]:
    """构造 TaskManager + 主 Agent；子 Agent 用独立 FakeProvider。"""
    provider = FakeProvider(provider_handler or (lambda n, m, t: [PTextDelta("好"), Done()]))
    registry = create_registry()
    parent = Agent(provider, registry, ChatSession())
    manager = TaskManager(
        roles=roles,
        providers=[_cfg()],
        default_config=_cfg(),
        default_provider=provider,
        registry=registry,
        hook_manager=None,
        file_rules=[],
        project_root=tmp_path,
    )
    return manager, provider, parent


def _finish(task: AgentTask, timeout: float = 10.0) -> None:
    assert task.done_event.wait(timeout), f"任务 #{task.id} 未在 {timeout}s 内完成"
    time.sleep(0.05)  # 让 finally 收尾（status 写入）完成


# ----------------------------------------------------------------------
# 定义式（F3）
# ----------------------------------------------------------------------


def test_defined_blank_session_and_role_prompt(tmp_path):
    seen: list = []

    def handler(n, messages, tools):
        seen.append((n, list(messages), {t.name for t in tools}))
        if n == 1:
            return [PTextDelta("审查完成"), Done()]
        return [PTextDelta(""), Done()]

    manager, provider, parent = _manager(
        tmp_path,
        {"reviewer": _role(tools=("read_file", "grep_code"))},
    )
    # 子 Agent 的 provider 与父不同：手工换用独立 FakeProvider
    sub = FakeProvider(handler)
    manager._default_provider = sub
    task, err = manager.start("defined", "reviewer", "审查这次改动", False, parent)
    assert err == ""
    _finish(task)
    assert task.status is TaskStatus.DONE
    assert task.result == "审查完成"
    # 空白会话：第一条消息是任务 prompt
    assert seen[0][1][0].text == "审查这次改动"
    # 工具集 = 白名单（不含 agent）
    assert seen[0][2] == {"read_file", "grep_code"}


def test_defined_role_body_in_system(tmp_path):
    systems: list = []

    def handler(n, messages, tools):
        return [PTextDelta("好"), Done()]

    manager, provider, parent = _manager(tmp_path, {"reviewer": _role(body="我是审查员")})
    sub = FakeProvider(handler)
    manager._default_provider = sub
    task, err = manager.start("defined", "reviewer", "任务", False, parent)
    assert err == ""
    _finish(task)
    assert "我是审查员" in sub.systems[0].dynamic


# ----------------------------------------------------------------------
# Fork 式（F4）
# ----------------------------------------------------------------------


def test_fork_inherits_messages_and_tools(tmp_path):
    seen: list = []

    def handler(n, messages, tools):
        seen.append((n, list(messages), {t.name for t in tools}))
        return [PTextDelta("排查完成"), Done()]

    manager, provider, parent = _manager(tmp_path, {})
    # 父对话预置消息
    parent._session.add_user("父问题")
    parent._session.add_assistant(text="父回复", tool_uses=[])
    fork_provider = FakeProvider(handler)
    manager._default_provider = fork_provider
    task, err = manager.start("fork", None, "继续深入排查", False, parent)
    assert err == ""
    assert task.background is True  # fork 强制后台
    _finish(task)
    msgs = seen[0][1]
    assert [m.text for m in msgs[:2]] == ["父问题", "父回复"]
    assert msgs[-1].text == "继续深入排查"
    # 工具集 = 父可见工具（FakeProvider 场景 = registry 全量减 agent）
    assert "agent" not in seen[0][2]


def test_fork_first_request_stable_matches_parent(tmp_path):
    manager, provider, parent = _manager(tmp_path, {})

    def handler(n, messages, tools):
        return [PTextDelta("好"), Done()]

    fork_provider = FakeProvider(handler)
    manager._default_provider = fork_provider
    parent._session.add_user("父问题")
    # 父 Agent 先跑一轮拿到 systems 供对比（直接调用 builder 拼装）
    parent_system = parent._builder.build([], 1)
    task, err = manager.start("fork", None, "排查", False, parent)
    assert err == ""
    _finish(task)
    # stable 逐字节一致（缓存命中前提）
    assert fork_provider.systems[0].stable == parent_system.stable


# ----------------------------------------------------------------------
# 三层防线（F7）
# ----------------------------------------------------------------------


def test_defenses_global_role_background(tmp_path):
    seen: list = []

    def handler(n, messages, tools):
        seen.append({t.name for t in tools})
        return [PTextDelta("好"), Done()]

    manager, provider, parent = _manager(tmp_path, {})
    # create_registry 本就不含 use_skill（装配层注册）——补一个假工具
    manager._registry.register(_FakeUseSkill())
    # 角色白名单故意含 agent 与 use_skill；黑名单剔除 grep_code
    role = _role(tools=("read_file", "grep_code", "agent", "use_skill"),
                 deny_tools=("grep_code",))
    manager._roles["r1"] = role
    sub = FakeProvider(handler)
    manager._default_provider = sub
    # 同步（非后台）：全局剔除 agent、角色黑名单剔除 grep_code；use_skill 保留
    task, err = manager.start("defined", "r1", "任务", False, parent)
    assert err == ""
    _finish(task)
    assert seen[0] == {"read_file", "use_skill"}
    # 后台：再剔除 use_skill
    task2, err2 = manager.start("defined", "r1", "任务2", True, parent)
    assert err2 == ""
    _finish(task2)
    assert seen[1] == {"read_file"}


# ----------------------------------------------------------------------
# 同步等待与转场（F8）
# ----------------------------------------------------------------------


def test_wait_sync_returns_result_when_done(tmp_path):
    manager, provider, parent = _manager(
        tmp_path, {"r": _role()},
    )

    def handler(n, messages, tools):
        return [PTextDelta("完成结果"), Done()]

    manager._default_provider = FakeProvider(handler)
    task, err = manager.start("defined", "r", "任务", False, parent)
    assert err == ""
    assert manager.wait_sync(task) == "完成结果"
    assert task.background is False  # 同步完成不进后台


def test_wait_sync_timeout_flips_background(tmp_path, monkeypatch):
    manager, provider, parent = _manager(tmp_path, {"r": _role()})

    def handler(n, messages, tools):
        time.sleep(2)  # 慢任务：模拟长运行
        return [PTextDelta("慢完成"), Done()]

    manager._default_provider = FakeProvider(handler)
    monkeypatch.setattr(TaskManager, "SYNC_TIMEOUT", 0.3)
    task, err = manager.start("defined", "r", "任务", False, parent)
    assert err == ""
    result = manager.wait_sync(task)
    assert "已自动转后台" in result
    assert task.background is True
    _finish(task)
    assert task.status is TaskStatus.DONE
    # 转后台后完成进通知队列
    drained = manager.drain_completed()
    assert [t.id for t in drained] == [task.id]


def test_detach_flips_to_background(tmp_path):
    manager, provider, parent = _manager(tmp_path, {"r": _role()})

    def handler(n, messages, tools):
        time.sleep(1)
        return [PTextDelta("慢完成"), Done()]

    manager._default_provider = FakeProvider(handler)
    task, err = manager.start("defined", "r", "任务", False, parent)
    assert err == ""
    assert manager.detach(task.id) is True
    result = manager.wait_sync(task)
    assert "已切后台" in result
    assert task.detached is True
    _finish(task)
    assert [t.id for t in manager.drain_completed()] == [task.id]


def test_background_limit(tmp_path, monkeypatch):
    manager, provider, parent = _manager(tmp_path, {"r": _role()})

    def handler(n, messages, tools):
        time.sleep(2)
        return [PTextDelta("慢"), Done()]

    manager._default_provider = FakeProvider(handler)
    for i in range(TaskManager.MAX_BACKGROUND):
        task, err = manager.start("defined", "r", f"任务{i}", True, parent)
        assert err == ""
    task, err = manager.start("defined", "r", "超限", True, parent)
    assert task is None
    assert "上限" in err


# ----------------------------------------------------------------------
# 角色不存在与 token 回报（F2/F5）
# ----------------------------------------------------------------------


def test_role_not_found_lists_available(tmp_path):
    manager, provider, parent = _manager(tmp_path, {"a": _role(name="a"), "b": _role(name="b")})
    task, err = manager.start("defined", "missing", "任务", False, parent)
    assert task is None
    assert "missing" in err
    assert "a" in err and "b" in err


def test_token_usage_reported(tmp_path):
    def handler(n, messages, tools):
        if n == 1:
            return [PTextDelta("工作"), ToolCall(id="t1", name="read_file",
                    args={"file_path": str(tmp_path / "x.py")}), Done()]
        return [PTextDelta("完成"), Done()]

    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")

    class UsageProvider(FakeProvider):
        def stream(self, messages, tools, system):
            # 覆写：给 Done 附 usage
            events = super().stream(messages, tools, system)
            for e in events:
                if isinstance(e, Done):
                    e.usage = UsageInfo(input_tokens=100 + self.calls, output_tokens=50)
                yield e

    manager, provider, parent = _manager(tmp_path, {"r": _role()})
    manager._default_provider = UsageProvider(handler)
    task, err = manager.start("defined", "r", "任务", False, parent)
    assert err == ""
    _finish(task)
    assert task.usage_in > 0
    assert task.usage_out > 0
