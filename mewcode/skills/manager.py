"""SkillManager：激活状态、钉住块快照、白名单并集、斜杠命令注册（ch13 F4/F5/F7）。

激活是会话级状态：
- activate 每次从磁盘重读入口文件重新解析（热更新），参数占位符替换后
  存入激活表；首次激活且已绑定命令注册中心时注册斜杠短命令（撞内置
  命令名则跳过并警告）
- active_snapshot 每轮由系统提示拼装现取（钉在补充块之后）
- whitelist 为激活技能白名单的并集（tools 为 None 的技能不收窄）
- clear 清空激活表、撤销已注册命令（/clear 联动）、清 pending
- pending 是独立执行的单槽登记：use_skill 工具登记，主循环轮末取走
"""

from __future__ import annotations

from dataclasses import dataclass

from mewcode.commands.registry import CommandRegistry, CommandSpec, CommandType
from mewcode.skills.model import Skill, parse_skill_file, substitute_args


@dataclass
class ActivationResult:
    """一次激活的产物：成败、参数替换后的正文、是否首次激活。"""

    ok: bool
    error: str = ""  # 失败原因（未知技能 / 重读失败）
    skill: Skill | None = None
    body: str = ""  # 参数替换后的 SOP
    first_time: bool = False  # 是否首次激活（命令注册去重用）


class SkillManager:
    """技能激活状态与运行时行为：纯逻辑，不依赖渲染框架。"""

    def __init__(self, skills: dict[str, Skill]) -> None:
        self.skills = skills  # 生效技能集（只读共享；独立对话复用同一字典）
        self._active: dict[str, tuple[Skill, str]] = {}  # name -> (Skill, 替换后正文)
        self._registry: CommandRegistry | None = None
        self._registered: set[str] = set()  # 已注册过斜杠命令的技能名（clear 撤销用）
        self._pending: tuple[str, str] | None = None  # (name, args) 单槽位
        self._warnings: list[str] = []  # 激活期警告（如命令撞名），调用方展示用

    # ---------- 激活 ----------

    def activate(self, name: str, args: str = "") -> ActivationResult:
        """激活技能：重读磁盘（热更新）→ 参数替换 → 存激活表 → 注册命令。"""
        skill = self.skills.get(name)
        if skill is None:
            return ActivationResult(ok=False, error=f"未知技能: {name!r}")
        # 热更新：每次从磁盘重读入口文件，编辑后再次加载即生效（AC6）
        refreshed, error = parse_skill_file(skill.source, skill.level, skill.dir)
        if error is not None:
            return ActivationResult(
                ok=False, error=f"技能 {name} 重读失败: {error}"
            )
        skill = refreshed
        body = substitute_args(skill.body, args)
        first_time = name not in self._active
        self._active[name] = (skill, body)
        if first_time:
            self._register_command(skill)
        return ActivationResult(ok=True, skill=skill, body=body, first_time=first_time)

    def _register_command(self, skill: Skill) -> None:
        """首次激活时注册斜杠短命令；撞内置命令名 → 跳过并警告（AC13）。"""
        if self._registry is None:
            return
        if self._registry.resolve(skill.name) is not None:
            self._warnings.append(f"技能命令 /{skill.name} 与内置命令撞名，跳过注册")
            return
        # 参数原样交给 run_skill（占位符在 activate 内替换）
        self._registry.register(
            CommandSpec(
                name=skill.name,
                description=skill.description,
                usage=f"/{skill.name} [参数]",
                type=CommandType.PROMPT,
                handler=lambda parsed, ui: ui.run_skill(skill.name, parsed.args),
            )
        )
        self._registered.add(skill.name)

    def attach_registry(self, registry: CommandRegistry) -> None:
        """绑定命令注册中心（启动装配时调用一次）。"""
        self._registry = registry

    # ---------- 每轮现取的上下文 ----------

    def active_snapshot(self) -> str:
        """【已激活技能】块：按激活顺序拼装，每轮由系统提示 build 现取。"""
        if not self._active:
            return ""
        lines = ["【已激活技能】"]
        for skill, body in self._active.values():
            lines.append(f"### {skill.name}")
            if skill.dir is not None:
                lines.append(f"技能目录: {skill.dir}")
            lines.append(body)
        return "\n".join(lines)

    # ---------- 工具白名单 ----------

    def whitelist_active(self) -> bool:
        """是否存在白名单收窄（任一激活技能声明了 tools）。"""
        return any(skill.tools is not None for skill, _ in self._active.values())

    def whitelist(self) -> frozenset[str]:
        """激活技能白名单的并集（tools 为 None 的技能不参与）。"""
        names: set[str] = set()
        for skill, _ in self._active.values():
            if skill.tools is not None:
                names.update(skill.tools)
        return frozenset(names)

    def is_allowed(self, tool_name: str) -> bool:
        """执行侧拦截判定：无收窄时恒 True。"""
        return (not self.whitelist_active()) or tool_name in self.whitelist()

    # ---------- 独立执行登记 ----------

    def set_pending(self, name: str, args: str) -> None:
        """登记待执行的独立技能（use_skill 工具调用）；单槽位，后写覆盖先写。"""
        self._pending = (name, args)

    def take_pending(self) -> tuple[str, str] | None:
        """取走并清空 pending（主循环轮末调用）。"""
        pending, self._pending = self._pending, None
        return pending

    def take_warnings(self) -> list[str]:
        """取走并清空激活期警告（use_skill 工具结果与 run_skill 展示用）。"""
        warnings, self._warnings = self._warnings, []
        return warnings

    # ---------- 生命周期 ----------

    def clear(self) -> None:
        """清空激活表、撤销已注册命令、清 pending 与警告（/clear 联动）。"""
        self._active.clear()
        self._pending = None
        self._warnings.clear()
        if self._registry is not None:
            for name in self._registered:
                self._registry.unregister(name)
        self._registered.clear()
