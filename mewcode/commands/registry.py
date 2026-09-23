"""命令注册中心：元数据登记、别名解析、启动期冲突校验（ch12 F1）。

校验规则：命令名非空且不含空白；命令名互不相同；别名互不相同；
别名不得撞任何命令名（含自身）。冲突详情由 validate 返回，
build_registry 抛 CommandConflictError，cli 层 stderr + exit(1)——
撞名在启动阶段直接退出，不等运行时再炸。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, Callable

from mewcode.commands.parser import ParsedCommand
from mewcode.commands.ui import UiController

if TYPE_CHECKING:
    from mewcode.commands.dispatch import CommandResult


class CommandType(Enum):
    """命令执行模式分类（ch12 F3）。"""

    LOCAL = auto()  # 纯本地：只读计算与展示
    UI = auto()  # 影响界面状态：改会话/模式/退出
    PROMPT = auto()  # 提示词：预设文本送进对话交给 AI


@dataclass(frozen=True)
class CommandSpec:
    """一条命令的完整元数据。"""

    name: str  # 主命令名（注册时归一为小写）
    description: str  # 简短描述（/help 与补全菜单用）
    usage: str = ""  # 用法示例，如 "/permission [show]"
    aliases: tuple[str, ...] = ()  # 别名（不含斜杠，注册时归一为小写）
    arg_hint: str = ""  # 参数提示（仅展示用，不做校验）
    type: CommandType = CommandType.LOCAL  # 执行模式分类
    hidden: bool = False  # 隐藏命令不参与补全与 /help
    handler: Callable[[ParsedCommand, UiController], CommandResult | None] | None = None


class CommandConflictError(Exception):
    """命令注册冲突：携带冲突详情文本，启动阶段抛出（ch12 F1）。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("\n".join(problems))


class CommandRegistry:
    """命令登记、冲突校验、别名解析与可见列表。"""

    def __init__(self) -> None:
        self._specs: list[CommandSpec] = []
        self._by_name: dict[str, CommandSpec] = {}
        self._alias_to_name: dict[str, str] = {}
        self._indexed = False  # 索引过期标记：register 后置 False

    def register(self, spec: CommandSpec) -> None:
        """登记一条命令；名称与别名归一为小写。"""
        normalized = spec
        if spec.name != spec.name.lower() or any(
            a != a.lower() for a in spec.aliases
        ):
            normalized = replace(
                spec,
                name=spec.name.lower(),
                aliases=tuple(a.lower() for a in spec.aliases),
            )
        self._specs.append(normalized)
        self._indexed = False

    def validate(self) -> list[str]:
        """整体校验，返回问题列表（空列表 = 通过）。"""
        problems: list[str] = []
        by_name: dict[str, CommandSpec] = {}
        alias_owner: dict[str, str] = {}
        for spec in self._specs:
            if not spec.name or any(c.isspace() for c in spec.name):
                problems.append(f"命令名非法：{spec.name!r}（空或含空白）")
                continue
            if spec.name in by_name:
                problems.append(f"命令名冲突：/{spec.name} 被重复注册")
            else:
                by_name[spec.name] = spec
            for alias in spec.aliases:
                if not alias or any(c.isspace() for c in alias):
                    problems.append(
                        f"别名非法：命令 /{spec.name} 的别名 {alias!r}（空或含空白）"
                    )
                elif alias in alias_owner:
                    problems.append(
                        f"别名冲突：/{alias} 同时是 /{alias_owner[alias]} 与 /{spec.name} 的别名"
                    )
                else:
                    alias_owner[alias] = spec.name
        # 第二遍交叉检查：别名撞命令名（含别名等于自身命令名）
        for alias, owner in alias_owner.items():
            if alias in by_name:
                problems.append(f"别名冲突：/{alias} 是 /{owner} 的别名，撞命令 /{alias}")
        return problems

    def resolve(self, name: str) -> CommandSpec | None:
        """按主名或别名查找（大小写不敏感）；未命中返回 None。"""
        self._ensure_index()
        name = name.lower()  # 解析器已小写，此处双保险
        return self._by_name.get(name) or (
            self._by_name.get(self._alias_to_name.get(name, ""))
        )

    def visible(self) -> list[CommandSpec]:
        """非隐藏命令，按名称排序（补全与 /help 的数据源）。"""
        self._ensure_index()
        return sorted((s for s in self._specs if not s.hidden), key=lambda s: s.name)

    def all(self) -> list[CommandSpec]:
        """全部命令（含隐藏，诊断用）。"""
        return list(self._specs)

    def _ensure_index(self) -> None:
        """构建名字/别名索引（validate 通过后的正常路径；重名时首个生效）。"""
        if self._indexed:
            return
        self._by_name = {}
        self._alias_to_name = {}
        for spec in self._specs:
            self._by_name.setdefault(spec.name, spec)
            for alias in spec.aliases:
                self._alias_to_name.setdefault(alias, spec.name)
        self._indexed = True
