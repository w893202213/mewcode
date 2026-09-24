"""/tasks 命令测试（ch16 F8）：列表渲染、detach、注册。"""

import time

from mewcode.agents.manager import AgentTask, TaskStatus
from mewcode.commands.builtins import (
    make_tasks_handler,
    render_task_list,
    register_tasks_command,
)
from mewcode.commands.dispatch import CommandResult, dispatch
from mewcode.commands.parser import ParsedCommand
from mewcode.commands.registry import CommandRegistry
from mewcode.commands.ui import UiController


class FakeUi(UiController):
    def __init__(self):
        self.displayed: list[str] = []

    def display(self, text: str) -> None:
        self.displayed.append(text)


class FakeManager:
    """最小任务管理器替身：snapshot/detach 可脚本化。"""

    def __init__(self, tasks=None, detach_result=True):
        self._tasks = tasks or []
        self.detach_result = detach_result
        self.detached_ids: list[int] = []

    def snapshot(self):
        return self._tasks

    def detach(self, task_id: int) -> bool:
        self.detached_ids.append(task_id)
        return self.detach_result


def _task(id=1, status=TaskStatus.RUNNING, role="reviewer", background=False,
          result=None, usage_in=100, usage_out=50):
    task = AgentTask(
        id=id, kind="defined", prompt="任务", role=role, status=status,
        started_at=time.monotonic() - 10,
    )
    task.usage_in = usage_in
    task.usage_out = usage_out
    task.background = background
    task.result = result
    return task


def test_render_empty_list():
    assert "没有进行中的子 Agent 任务" in render_task_list([])


def test_render_tasks_with_status():
    text = render_task_list(
        [
            _task(id=1, status=TaskStatus.RUNNING, role="reviewer"),
            _task(id=2, status=TaskStatus.DONE, role="general", result="审查通过"),
        ]
    )
    assert "#1" in text and "reviewer" in text and "运行中" in text
    assert "#2" in text and "已完成" in text and "审查通过" in text
    assert "入100/出50" in text


def test_handler_detach_valid_id():
    manager = FakeManager()
    ui = FakeUi()
    handler = make_tasks_handler(manager)
    result = handler(ParsedCommand(name="tasks", args="detach 3"), ui)
    assert result is None
    assert manager.detached_ids == [3]
    assert "已切后台" in ui.displayed[0]


def test_handler_detach_invalid_id():
    manager = FakeManager(detach_result=False)
    ui = FakeUi()
    handler = make_tasks_handler(manager)
    handler(ParsedCommand(name="tasks", args="detach 9"), ui)
    assert "无法切换" in ui.displayed[0]


def test_handler_detach_non_numeric():
    ui = FakeUi()
    handler = make_tasks_handler(FakeManager())
    handler(ParsedCommand(name="tasks", args="detach abc"), ui)
    assert "用法" in ui.displayed[0]


def test_handler_plain_lists_tasks():
    ui = FakeUi()
    handler = make_tasks_handler(FakeManager([_task(id=1)]))
    handler(ParsedCommand(name="tasks", args=""), ui)
    assert "#1" in ui.displayed[0]


def test_register_tasks_command_visible_in_help():
    registry = CommandRegistry()
    from mewcode.commands.builtins import register_builtins

    register_builtins(registry)
    register_tasks_command(registry, FakeManager())
    names = [spec.name for spec in registry.visible()]
    assert "tasks" in names
