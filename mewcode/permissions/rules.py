"""规则引擎：Rule/RuleSet 数据结构、匹配与裁决、三 YAML 读写合并。

规则形式「工具名(模式)」：tool 为任意工具名（六个内置工具或 MCP
工具），pattern 为精确字符串或 glob（`*`/`?`，`**` 归一为 `*`、
跨路径分隔符匹配）。裁决三种：allow / ask / deny；多条命中时
deny > ask > allow（与所在层级无关）。

三个 YAML 文件（用户级/项目级/本地级）启动时合并读取，层级只决定
写入位置：永久允许一律追加到本地级。文件规则的工具名在加载时只查
格式（非空字符串），存在性由 validate_tools 在工具注册完成后统一
校验（ch08 F8：MCP 工具名注册前未知）。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml

from mewcode.config import ConfigError


class Action(Enum):
    """规则裁决结果。"""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass
class Rule:
    """一条规则：某工具 + 模式 → 裁决。"""

    tool: str
    pattern: str
    action: Action


def _pattern_matches(pattern: str, value: str) -> bool:
    """模式匹配：先精确相等，再 glob（fnmatch，`*` 跨路径分隔符）。

    `**/` 表示零或多层目录，归一为 `*`；剩余 `**` 归一为 `*`。
    """
    if pattern == value:
        return True
    normalized = pattern.replace("**/", "*").replace("**", "*")
    return fnmatch.fnmatch(value, normalized)


class RuleSet:
    """规则集合：三文件合并 + 会话规则；匹配与裁决。"""

    def __init__(self) -> None:
        self._rules: list[Rule] = []
        self._file_rules: list[Rule] = []  # 仅文件来源（validate_tools 校验对象）
        self._local_path: Path | None = None

    @property
    def rules(self) -> list[Rule]:
        """当前全部规则（副本，防外部改动）。"""
        return list(self._rules)

    def add(self, rule: Rule) -> None:
        """追加一条规则（会话规则与文件规则同集合，顺序不影响裁决）。"""
        self._rules.append(rule)

    def match(self, tool: str, value: str) -> list[Action]:
        """返回该工具+值命中的全部规则裁决（按注册顺序）。"""
        return [
            rule.action
            for rule in self._rules
            if rule.tool == tool and _pattern_matches(rule.pattern, value)
        ]

    def verdict(self, tool: str, value: str, default: Action) -> Action:
        """合并裁决：deny > ask > allow；无命中 → default。"""
        actions = self.match(tool, value)
        if Action.DENY in actions:
            return Action.DENY
        if Action.ASK in actions:
            return Action.ASK
        if Action.ALLOW in actions:
            return Action.ALLOW
        return default

    # ------------------------------------------------------------------
    # 三 YAML 读写（F3）
    # ------------------------------------------------------------------

    def load_from(self, paths: list[Path], local_path: Path) -> None:
        """依次读取各规则文件并合并；缺失跳过，损坏/非法条目报 ConfigError。

        local_path 为「永久允许」的写入目标（本地级），单独记录。
        """
        for path in paths:
            self._load_file(path)
        self._local_path = local_path

    @staticmethod
    def _parse_rule_file(path: Path) -> list[Rule]:
        """解析一个 YAML 规则文件为 Rule 列表；校验失败抛 ConfigError。"""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigError(f"权限规则文件格式错误: {path} ({e})") from e
        if data is None:
            return []
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise ConfigError(f"权限规则文件结构错误: {path}（顶层需要 rules 列表）")
        rules: list[Rule] = []
        for i, item in enumerate(data["rules"], start=1):
            if not isinstance(item, dict):
                raise ConfigError(f"权限规则文件 {path} 第 {i} 条不是映射")
            tool = item.get("tool")
            pattern = item.get("pattern")
            action = item.get("action")
            if not isinstance(tool, str) or not tool:
                raise ConfigError(f"权限规则文件 {path} 第 {i} 条 tool 无效: {tool!r}")
            if not isinstance(pattern, str) or not pattern:
                raise ConfigError(f"权限规则文件 {path} 第 {i} 条 pattern 缺失")
            try:
                action_enum = Action(str(action).strip().lower())
            except ValueError:
                raise ConfigError(
                    f"权限规则文件 {path} 第 {i} 条 action 无效: {action!r}（可选: allow/ask/deny）"
                ) from None
            rules.append(Rule(tool=tool, pattern=pattern, action=action_enum))
        return rules

    def _load_file(self, path: Path) -> None:
        """读一个文件：缺失跳过，存在则解析校验并合并。"""
        if not path.is_file():
            return
        rules = self._parse_rule_file(path)
        self._rules.extend(rules)
        self._file_rules.extend(rules)

    def validate_tools(self, known: set[str]) -> None:
        """校验文件来源规则的工具名都在已知工具集合内。

        会话规则（add 进来的）不参与——它们由真实调用构造，天然合法。
        在 MCP 工具注册完成后调用（ch08 F8）。
        """
        invalid = sorted({r.tool for r in self._file_rules if r.tool not in known})
        if invalid:
            raise ConfigError(
                "权限规则引用了未注册的工具: " + ", ".join(repr(t) for t in invalid)
            )

    def append_to_local_file(self, rule: Rule) -> None:
        """把规则追加写进本地级 YAML（去重）；本地级缺失时新建。

        用于「永久允许」：读当前内容 → 追加 → 写回，保留既有规则。
        """
        if self._local_path is None:
            raise ConfigError("未配置本地级规则文件路径，无法写入永久规则")
        existing = (
            self._parse_rule_file(self._local_path)
            if self._local_path.is_file()
            else []
        )
        if any(
            r.tool == rule.tool and r.pattern == rule.pattern and r.action == rule.action
            for r in existing
        ):
            return  # 相同规则已存在，幂等
        merged = existing + [rule]
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rules": [
                {"tool": r.tool, "pattern": r.pattern, "action": r.action.value}
                for r in merged
            ]
        }
        self._local_path.write_text(
            yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
