"""Hook 规则加载：数据结构定义 + YAML 解析与集中校验（ch15 F1/F8）。

三要素规则「event + if + action」从 config.yaml 顶层 `hooks:` 列表加载，
启动时逐条校验，任一非法抛 ConfigError（中文、含规则序号，fail-closed
口径与现有配置一致）。解析（parse_hooks）与文件读取（load_hooks）分离，
便于测试直接喂已解析的 YAML 数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from mewcode.config import ConfigError

# 十个生命周期事件（spec F2）：会话/轮次/消息/工具四层 + 系统级
HOOK_EVENTS = (
    "session_start",
    "session_end",
    "round_start",
    "round_end",
    "user_message",
    "assistant_message",
    "tool_before",
    "tool_after",
    "startup",
    "shutdown",
)

# 拦截类事件：动作结果能产生「拒绝」语义，禁止 background（spec F4/F6）
INTERCEPT_EVENTS = {"tool_before"}

# 每事件的条件允许字段（field 首段）；args./result. 后缀段运行时点路径判定
EVENT_FIELDS: dict[str, set[str]] = {
    "startup": {"event"},
    "shutdown": {"event"},
    "session_start": {"event"},
    "session_end": {"event"},
    "round_start": {"event", "round"},
    "round_end": {"event", "round", "reason"},
    "user_message": {"event", "round"},
    "assistant_message": {"event", "round"},
    "tool_before": {"event", "round", "tool", "args"},
    "tool_after": {"event", "round", "tool", "args", "result"},
}

ACTION_TYPES = ("command", "inject", "http", "subagent")
HTTP_METHODS = {"GET", "POST", "PUT", "DELETE"}
DEFAULT_TIMEOUT = 30.0  # 秒；命令/HTTP 动作缺省超时（spec N2）


@dataclass
class HookCondition:
    """一条 if 条件：某字段 + 模式单串（四种匹配语法见 matcher）。"""

    field: str  # 点路径："tool"、"args.command"、"result.ok"
    pattern: str  # 单串四模式："git status" / "*.md" / "/^rm /" / "!/tmp/*"


@dataclass
class HookAction:
    """一个动作：type 决定取用哪组字段（spec F5）。"""

    type: str  # "command" | "inject" | "http" | "subagent"
    run: str = ""  # command 的命令串
    prompt: str = ""  # inject 的静态文本 / subagent 占位提示
    url: str = ""  # http 的地址（仅 http/https）
    method: str = "GET"  # GET/POST/PUT/DELETE
    headers: dict = field(default_factory=dict)
    body: str = ""  # http 请求体


@dataclass
class HookRule:
    """一条完整规则：事件 + 条件（可空）+ 动作 + 执行控制。"""

    index: int  # 配置序号（1 起，日志与报错定位）
    event: str  # 10 事件名之一
    conditions: list[HookCondition]
    match: str  # "all" | "any"；条件为空时恒 True
    action: HookAction
    run_once: bool = False  # 本次进程运行期间只执行一次
    background: bool = False  # 后台异步执行（拦截类事件禁止）
    timeout: float = DEFAULT_TIMEOUT  # 秒；仅 command/http 生效
