"""Agent 技能白名单测试（ch13 F5/AC7/AC9）：可见工具收窄、执行侧拦截。"""

import asyncio
from pathlib import Path

from mewcode.agent.loop import Agent
from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.provider.base import Done, TextDelta, ToolCall
from mewcode.session import ChatSession
from mewcode.skills.manager import SkillManager
from mewcode.skills.model import parse_skill_file
from mewcode.skills.use_skill import UseSkillTool
from mewcode.tools import ToolRegistry, create_registry


def _registry_with(manager: SkillManager) -> ToolRegistry:
    """生产形态的注册中心：六个核心工具 + use_skill（cli 装配同款）。"""
    registry = create_registry()
    registry.register(UseSkillTool(manager))
    return registry


class FakeProvider:
    """脚本化假 Provider：handler(第几次调用, messages, tools) → 事件列表。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls = 0

    def stream(self, messages, tools, system):
        self.calls += 1
        return iter(self._handler(self.calls, messages, tools))


def _collect(agent: Agent, text: str) -> list:
    async def go():
        return [event async for event in agent.run(text)]

    return asyncio.run(go())


def _manager_with(tmp_path, name="commit", tools="[run_command]", body="SOP"):
    path = tmp_path / f"{name}.md"
    path.write_text(
        f"---\nname: {name}\ndescription: 说明\ntools: {tools}\n---\n{body}\n",
        encoding="utf-8",
    )
    skill, error = parse_skill_file(path, "builtin")
    assert error is None
    return SkillManager({name: skill})


def _tool_names(tools) -> list[str]:
    return [t.name for t in tools]


def test_visible_tools_narrowed_by_whitelist(tmp_path):
    """AC7：激活带白名单技能后，模型收到的工具 = 白名单 ∪ use_skill。"""
    manager = _manager_with(tmp_path, tools="[run_command]")
    seen = {}

    def handler(n, messages, tools):
        seen["tools"] = _tool_names(tools)
        return [TextDelta("完成"), Done()]

    agent = Agent(FakeProvider(handler), _registry_with(manager), ChatSession(), skill_manager=manager)
    manager.activate("commit")
    _collect(agent, "帮我提交")
    assert seen["tools"] == ["run_command", "use_skill"]  # 排序后：run_command < use_skill


def test_no_active_skill_all_tools_visible(tmp_path):
    manager = _manager_with(tmp_path)
    seen = {}

    def handler(n, messages, tools):
        seen["tools"] = _tool_names(tools)
        return [TextDelta("好"), Done()]

    agent = Agent(FakeProvider(handler), _registry_with(manager), ChatSession(), skill_manager=manager)
    _collect(agent, "你好")
    # 无激活技能 → 不收窄：六个核心工具 + use_skill 全部可见
    assert len(seen["tools"]) == 7
    assert "use_skill" in seen["tools"]


def test_whitelist_union_two_skills(tmp_path):
    """AC7：两个激活技能白名单取并集。"""
    path_a = tmp_path / "a.md"
    path_a.write_text(
        "---\nname: a\ndescription: 说明\ntools: [run_command]\n---\nA\n",
        encoding="utf-8",
    )
    path_b = tmp_path / "b.md"
    path_b.write_text(
        "---\nname: b\ndescription: 说明\ntools: [read_file]\n---\nB\n",
        encoding="utf-8",
    )
    skills = {}
    for p in (path_a, path_b):
        skill, error = parse_skill_file(p, "builtin")
        assert error is None
        skills[skill.name] = skill
    manager = SkillManager(skills)
    manager.activate("a")
    manager.activate("b")
    seen = {}

    def handler(n, messages, tools):
        seen["tools"] = _tool_names(tools)
        return [TextDelta("好"), Done()]

    agent = Agent(FakeProvider(handler), _registry_with(manager), ChatSession(), skill_manager=manager)
    _collect(agent, "你好")
    assert seen["tools"] == ["read_file", "run_command", "use_skill"]


def test_execution_side_blocks_non_whitelisted_tool(tmp_path):
    """AC9：模型幻觉调用白名单外工具 → 回灌失败结果，不执行。"""
    manager = _manager_with(tmp_path, tools="[run_command]")
    manager.activate("commit")
    result_texts = {}

    def handler(n, messages, tools):
        if n == 1:
            # 模型幻觉调用白名单外的 read_file
            return [ToolCall(id="c1", name="read_file", args={"path": str(tmp_path / "x.txt")})]
        # 回灌后模型收手
        result_texts["blocked"] = True
        return [TextDelta("好的"), Done()]

    agent = Agent(FakeProvider(handler), _registry_with(manager), ChatSession(), skill_manager=manager)
    events = _collect(agent, "帮我")
    tool_results = [
        e.result.content for e in events if type(e).__name__ == "ToolResultEvent"
    ]
    assert any("技能白名单未包含该工具: read_file" in t for t in tool_results)
    # 白名单内的 run_command 正常执行（不被拦截）——用真实副作用验证
    manager2 = _manager_with(tmp_path, name="c2", tools="[read_file]")
    manager2.activate("c2")
    target = tmp_path / "x.txt"
    target.write_text("内容", encoding="utf-8")

    def handler2(n, messages, tools):
        if n == 1:
            return [ToolCall(id="c1", name="read_file", args={"file_path": str(target)})]
        return [TextDelta("读到"), Done()]

    # 闸门根设为 tmp_path，避开项目根沙箱（既有测试不校验结果内容所以能过，这里要断言成功）
    gate2 = PermissionGate(tmp_path.resolve(), RuleSet(), Mode.DEFAULT)
    agent2 = Agent(
        FakeProvider(handler2),
        create_registry(),
        ChatSession(),
        gate=gate2,
        skill_manager=manager2,
    )
    events2 = _collect(agent2, "读文件")
    results2 = [
        e.result for e in events2 if type(e).__name__ == "ToolResultEvent"
    ]
    assert results2 and results2[0].ok and "内容" in results2[0].content


def test_plan_mode_and_whitelist_stack(tmp_path):
    """计划模式只读过滤与白名单收窄叠加。"""
    manager = _manager_with(tmp_path, tools="[run_command, read_file]")
    manager.activate("commit")
    seen = {}

    def handler(n, messages, tools):
        seen["tools"] = _tool_names(tools)
        return [TextDelta("好"), Done()]

    agent = Agent(FakeProvider(handler), _registry_with(manager), ChatSession(), skill_manager=manager)
    agent.enter_plan_mode()
    _collect(agent, "计划")
    # 白名单内只读的 read_file 保留 + use_skill 系统级可见；run_command 被计划模式滤掉
    assert seen["tools"] == ["read_file", "use_skill"]
