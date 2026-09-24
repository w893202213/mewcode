"""SubAgent 任务管理器：任务生命周期、子 Agent 构建与执行引擎（ch16）。

主 Agent 经统一 agent 工具委派子任务：定义式（角色 frontmatter 定义，
空白对话启动）与 Fork 式（继承父对话历史与工具集，首请求命中缓存）。
每个任务在专用线程 + 专属事件循环里「跑到底」非交互执行；同步/后台
只差等待方式——同步等待 60s 超时自动转后台，/tasks detach 手动切。
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from mewcode.agent.events import RoundEnd, UsageEvent
from mewcode.agent.loop import Agent
from mewcode.agents.model import AgentRole
from mewcode.config import ProviderConfig
from mewcode.permissions import Mode, PermissionGate, RuleSet
from mewcode.provider import create_provider
from mewcode.provider.base import Done, Provider, TextDelta
from mewcode.session import ChatSession
from mewcode.tools.files import FileReadCache
from mewcode.tools.registry import ToolRegistry


class TaskStatus(Enum):
    """任务状态三态。"""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass
class AgentTask:
    """一个子 Agent 任务的运行时状态。"""

    id: int  # 任务序号（1 起，进程内唯一）
    kind: str  # "defined" | "fork"
    prompt: str  # 任务描述（run 的第一条用户消息）
    role: str | None  # 角色名（fork 为 None）
    status: TaskStatus
    started_at: float  # time.monotonic
    finished_at: float | None = None
    usage_in: int = 0  # token 用量（任务线程消费 UsageEvent 累计）
    usage_out: int = 0
    result: str | None = None  # 最终回复 / 失败说明
    background: bool = False  # 是否已进入后台
    detached: bool = False  # 手动切后台标记
    done_event: threading.Event = field(default_factory=threading.Event)


# 再委派工具：任何子 Agent 一律剔除（全局禁止），后台任务再保险一层
DELEGATION_TOOLS = {"agent"}
# 后台任务额外剔除的再委派工具
BACKGROUND_EXCLUDED_TOOLS = {"agent", "use_skill"}


class TaskManager:
    """子 Agent 任务的构建与调度（ch16 F3/F4/F8）。"""

    SYNC_TIMEOUT = 60.0  # 同步等待上限（秒），超时自动转后台
    MAX_BACKGROUND = 5  # 同时运行的后台任务上限

    def __init__(
        self,
        roles: dict[str, AgentRole],
        providers: list[ProviderConfig],
        default_config: ProviderConfig,
        default_provider: Provider,
        registry: ToolRegistry,
        hook_manager,
        file_rules: list,
        project_root: Path,
        memory_loader=None,
    ) -> None:
        self._roles = roles
        self._providers = providers
        self._default_config = default_config
        self._default_provider = default_provider
        self._registry = registry
        self._hook_manager = hook_manager
        self._file_rules = file_rules
        self._project_root = project_root
        self._memory_loader = memory_loader
        self._tasks: list[AgentTask] = []
        self._completed: queue.Queue[AgentTask] = queue.Queue()
        self._next_id = 1

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def start(
        self,
        kind: str,
        role: str | None,
        prompt: str,
        background: bool,
        parent_agent,
    ) -> tuple[AgentTask | None, str]:
        """启动一个子 Agent 任务；返回 (任务, "") 或 (None, 失败原因)。"""
        role_obj: AgentRole | None = None
        if kind == "defined":
            role_obj = self._roles.get(role or "")
            if role_obj is None:
                names = ", ".join(sorted(self._roles))
                return None, f"角色 {role!r} 不存在，可用角色: {names}"
        if background and self._background_count() >= self.MAX_BACKGROUND:
            return None, (
                f"后台任务已达上限（{self.MAX_BACKGROUND} 个），"
                "请等待已有任务完成后再委派"
            )
        task = AgentTask(
            id=self._next_id,
            kind=kind,
            prompt=prompt,
            role=role if kind == "defined" else None,
            status=TaskStatus.RUNNING,
            started_at=time.monotonic(),
            background=bool(background) or kind == "fork",
        )
        self._next_id += 1
        try:
            if kind == "defined":
                agent = self._build_role_agent(
                    role_obj, prompt, parent_agent, task.id,
                    background=task.background,
                )
            else:
                agent = self._build_fork_agent(prompt, parent_agent, task.id)
        except Exception as e:
            return None, f"子 Agent 构建失败: {e}"
        self._tasks.append(task)
        threading.Thread(
            target=self._task_main,
            args=(task, agent),
            daemon=True,
            name=f"subagent-{task.id}",
        ).start()
        return task, ""

    def detach(self, task_id: int) -> bool:
        """手动把进行中的同步任务切后台（/tasks detach）。"""
        for task in self._tasks:
            if task.id == task_id and task.status is TaskStatus.RUNNING:
                task.detached = True
                task.background = True
                return True
        return False

    def snapshot(self) -> list[AgentTask]:
        """当前全部任务（副本列表，按 id 排序）。"""
        return sorted(self._tasks, key=lambda t: t.id)

    def drain_completed(self) -> list[AgentTask]:
        """取走已完成的后台任务（结果注入主历史用，全取全清）。"""
        drained: list[AgentTask] = []
        while not self._completed.empty():
            try:
                drained.append(self._completed.get_nowait())
            except queue.Empty:
                break
        return drained

    # ------------------------------------------------------------------
    # 子 Agent 构建（F3/F4/F7）
    # ------------------------------------------------------------------

    def _background_count(self) -> int:
        return sum(
            1
            for t in self._tasks
            if t.status is TaskStatus.RUNNING and t.background
        )

    def _apply_defenses(
        self, names: set[str], role: AgentRole | None, background: bool
    ) -> set[str]:
        """三层防线（F7）：① 全局剔除再委派；② 角色黑名单；③ 后台再剔。"""
        result = names - DELEGATION_TOOLS
        if role is not None and role.deny_tools:
            result.difference_update(role.deny_tools)
        if background:
            result.difference_update(BACKGROUND_EXCLUDED_TOOLS)
        return result

    def _build_gate(self, permission: str | None, parent_mode) -> PermissionGate:
        """独立权限闸门：新 RuleSet 只装文件规则（会话规则不传入，F5）。"""
        rules = RuleSet()
        rules.add_all(self._file_rules)
        mode = Mode(permission) if permission is not None else parent_mode
        return PermissionGate(self._project_root, rules, mode)

    def _resolve_provider(self, role: AgentRole):
        """模型解析：None=父 provider；按 providers 名字匹配；再按模型 id 派生。"""
        if role.model is None:
            return self._default_provider
        for cfg in self._providers:
            if cfg.name == role.model:
                return create_provider(cfg)
        derived = replace(self._default_config, model=role.model)
        return create_provider(derived)

    def _context_dir(self, task_id: int) -> Path:
        """独立存盘子目录：避免多任务与外层 ResultStore 序号撞车（ch13 同款）。"""
        return self._project_root / ".mewcode" / "context" / "agents" / f"task_{task_id}"

    def _build_role_agent(
        self, role: AgentRole, prompt: str, parent_agent, task_id: int,
        background: bool = False,
    ) -> Agent:
        """定义式（F3）：空白会话 + 角色正文进自定义指令块 + 角色工具集。"""
        session = ChatSession()  # 任务消息由 run(task.prompt) 加入
        registry_names = {t.name for t in self._registry.list()}
        if role.tools is not None:
            names = set(role.tools) & registry_names
        else:
            names = set(registry_names)
        allowed = self._apply_defenses(names, role, background=background)
        parent_mode = getattr(parent_agent, "permission_mode", Mode.DEFAULT)
        return Agent(
            self._resolve_provider(role),
            self._registry,
            session,
            max_iterations=role.max_rounds,
            custom_instructions=role.body,
            gate=self._build_gate(role.permission, parent_mode),
            context_window=self._default_config.context_window,
            context_dir=self._context_dir(task_id),
            memory_loader=self._memory_loader,
            interactive=False,
            allowed_tools=allowed,
            read_cache=FileReadCache(),
            hook_manager=self._hook_manager,
        )

    def _build_fork_agent(self, prompt: str, parent_agent, task_id: int) -> Agent:
        """Fork 式（F4）：全量继承父消息与工具集；stable 参数与父一致。"""
        ctx = parent_agent.subagent_context()
        session = ChatSession()
        session.restore(ctx["messages"])  # 任务消息由 run(task.prompt) 加入
        names = {t.name for t in ctx["visible_tools"]}
        allowed = self._apply_defenses(names, None, background=True)
        return Agent(
            self._default_provider,
            self._registry,
            session,
            custom_instructions=ctx["custom_instructions"],
            gate=self._build_gate(None, ctx["permission_mode"]),
            context_window=self._default_config.context_window,
            context_dir=self._context_dir(task_id),
            memory_loader=ctx["memory_loader"],
            skills_summary=ctx["skills_summary"],
            interactive=False,
            allowed_tools=allowed,
            read_cache=FileReadCache(),
            hook_manager=self._hook_manager,
        )

    # ------------------------------------------------------------------
    # 执行引擎（F6/F8）
    # ------------------------------------------------------------------

    def _task_main(self, task: AgentTask, agent: Agent) -> None:
        """任务线程体：专属事件循环跑子 Agent，事件只消费不渲染。"""
        try:
            asyncio.run(self._consume(agent, task))
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.result = f"子 Agent 异常: {e}"
        finally:
            task.finished_at = time.monotonic()
            task.done_event.set()
            if task.background:
                # 后台任务的结果走完成队列（注入主历史）；同步完成由
                # 工具结果直接回灌，不进队列
                self._completed.put(task)

    async def _consume(self, agent: Agent, task: AgentTask) -> None:
        """消费子 Agent 事件流：累计 token 用量、收集最终回复。"""
        collected: list[str] = []
        async for event in agent.run(task.prompt):
            if isinstance(event, UsageEvent):
                task.usage_in = event.input_tokens
                task.usage_out = event.output_tokens
            elif isinstance(event, TextDelta):
                collected.append(event.text)
            elif isinstance(event, RoundEnd):
                if event.reason.value == "ok":
                    task.status = TaskStatus.DONE
                    task.result = "".join(collected).strip()
                else:
                    task.status = TaskStatus.FAILED
                    task.result = event.message or f"子 Agent 结束异常: {event.reason.value}"
                return

    def wait_sync(self, task: AgentTask) -> str:
        """同步等待（AgentTool.execute 线程内调用）：轮询至 60s。

        完成→结果文本；detach→已切后台；超时→自动转后台（任务线程继续跑）。
        """
        deadline = time.monotonic() + self.SYNC_TIMEOUT
        while time.monotonic() < deadline:
            if task.status is not TaskStatus.RUNNING:
                return task.result or ""
            if task.detached:
                return f"任务 #{task.id} 已切后台，完成后会通知主对话"
            time.sleep(0.2)
        task.background = True
        return (
            f"任务 #{task.id} 同步等待超过 {self.SYNC_TIMEOUT:g} 秒，"
            "已自动转后台，完成后会通知主对话"
        )
