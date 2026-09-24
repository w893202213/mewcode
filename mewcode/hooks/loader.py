"""Hook 规则加载：数据结构定义 + YAML 解析与集中校验（ch15 F1/F8）。

三要素规则「event + if + action」从 config.yaml 顶层 `hooks:` 列表加载，
启动时逐条校验，任一非法抛 ConfigError（中文、含规则序号，fail-closed
口径与现有配置一致）。解析（parse_hooks）与文件读取（load_hooks）分离，
便于测试直接喂已解析的 YAML 数据。
"""

from __future__ import annotations

import re
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
    prompt: str = ""  # inject 的静态文本 / subagent 的任务描述
    role: str = ""  # subagent 的角色名（ch16；空=缺省 general）
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


# ----------------------------------------------------------------------
# YAML 解析与集中校验（F8）
# ----------------------------------------------------------------------


def _err(num: int, detail: str) -> ConfigError:
    """统一的规则校验错误：中文 + 规则序号定位。"""
    return ConfigError(f"Hook 规则第 {num} 条: {detail}")


def _parse_condition(cond, event: str, num: int) -> HookCondition:
    """校验并解析一条 if 条件（field 越界、pattern 正则编译在此检查）。"""
    if not isinstance(cond, dict):
        raise _err(num, "条件必须是映射")
    if "all" in cond or "any" in cond:
        raise _err(num, "条件组合不允许嵌套")
    field = cond.get("field")
    pattern = cond.get("pattern")
    if not isinstance(field, str) or not field:
        raise _err(num, f"条件 field 无效: {field!r}")
    if field.split(".")[0] not in EVENT_FIELDS[event]:
        allowed = ", ".join(sorted(EVENT_FIELDS[event]))
        raise _err(num, f"事件 {event} 不支持条件字段 {field!r}（可选: {allowed}）")
    if not isinstance(pattern, str) or not pattern:
        raise _err(num, f"条件 pattern 无效: {pattern!r}")
    if len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/"):
        try:
            re.compile(pattern[1:-1])
        except re.error as e:
            raise _err(num, f"正则 pattern 编译失败 {pattern!r}: {e}") from e
    return HookCondition(field=field, pattern=pattern)


def _parse_conditions(if_val, event: str, num: int) -> tuple[list[HookCondition], str]:
    """解析 if：缺失→无条件；单条映射→单条件；all/any 键→组合（互斥、非空）。"""
    if if_val is None:
        return [], ""
    if not isinstance(if_val, dict):
        raise _err(num, "if 必须是条件映射或含 all/any 的组合")
    has_all, has_any = "all" in if_val, "any" in if_val
    if has_all or has_any:
        if has_all == has_any:
            raise _err(num, "if 的 all/any 必须二选一")
        key = "all" if has_all else "any"
        lst = if_val[key]
        if not isinstance(lst, list) or not lst:
            raise _err(num, f"if 的 {key} 必须是非空条件列表")
        return [_parse_condition(c, event, num) for c in lst], key
    # 单条条件映射
    return [_parse_condition(if_val, event, num)], "all"


def _parse_action(act, num: int) -> HookAction:
    """校验并解析 action：按 type 检查各自字段（F5）。"""
    if not isinstance(act, dict):
        raise _err(num, "action 必须是映射")
    atype = act.get("type")
    if atype not in ACTION_TYPES:
        raise _err(num, f"action.type 无效: {atype!r}（可选: {', '.join(ACTION_TYPES)}）")
    if atype == "command":
        run = act.get("run")
        if not isinstance(run, str) or not run.strip():
            raise _err(num, f"command 动作需要非空 run: {run!r}")
        return HookAction(type="command", run=run)
    if atype in ("inject", "subagent"):
        prompt = act.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise _err(num, f"{atype} 动作需要非空 prompt: {prompt!r}")
        role = act.get("role", "")
        if not isinstance(role, str):
            raise _err(num, f"{atype} 动作的 role 必须是字符串: {role!r}")
        return HookAction(type=atype, prompt=prompt, role=role)
    # http
    url = act.get("url")
    if not isinstance(url, str) or not (
        url.startswith("http://") or url.startswith("https://")
    ):
        raise _err(num, f"http 动作需要 http/https 开头的 url: {url!r}")
    method = act.get("method", "GET")
    if method not in HTTP_METHODS:
        raise _err(num, f"http 动作的 method 无效: {method!r}（可选: {', '.join(sorted(HTTP_METHODS))}）")
    headers = act.get("headers", {})
    if not isinstance(headers, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
    ):
        raise _err(num, f"http 动作的 headers 必须是字符串键值映射: {headers!r}")
    body = act.get("body", "")
    if not isinstance(body, str):
        raise _err(num, f"http 动作的 body 必须是字符串: {body!r}")
    return HookAction(
        type="http", url=url, method=method, headers=dict(headers), body=body
    )


def parse_hooks(data: dict) -> list[HookRule]:
    """从已解析的 YAML 数据校验并构造规则列表（F8）。

    顶层无 hooks 键或空列表 → 空列表（无 Hook 运行）；任一规则非法抛
    ConfigError（中文、含规则序号）。
    """
    items = data.get("hooks", [])
    if not items:
        return []
    if not isinstance(items, list):
        raise ConfigError("hooks 必须是列表")
    rules: list[HookRule] = []
    for i, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise _err(i, "规则必须是映射")
        event = item.get("event")
        if event not in HOOK_EVENTS:
            raise _err(i, f"event 无效: {event!r}（可选: {', '.join(HOOK_EVENTS)}）")
        conditions, match = _parse_conditions(item.get("if"), event, i)
        action = _parse_action(item.get("action"), i)
        run_once = item.get("run_once", False)
        if not isinstance(run_once, bool):
            raise _err(i, f"run_once 必须是布尔值: {run_once!r}")
        background = item.get("background", False)
        if not isinstance(background, bool):
            raise _err(i, f"background 必须是布尔值: {background!r}")
        if background and event in INTERCEPT_EVENTS:
            raise _err(i, f"拦截类事件 {event} 不允许 background（拦截必须同步拿结果）")
        timeout = item.get("timeout", DEFAULT_TIMEOUT)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise _err(i, f"timeout 必须是正数: {timeout!r}")
        rules.append(
            HookRule(
                index=i,
                event=event,
                conditions=conditions,
                match=match,
                action=action,
                run_once=run_once,
                background=background,
                timeout=float(timeout),
            )
        )
    return rules


def load_hooks(path: str | Path) -> list[HookRule]:
    """从 config.yaml 读取顶层 hooks 列表并校验。

    文件缺失、hooks 缺失或为空 → 空列表；YAML 损坏 → ConfigError。
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        return []
    except yaml.YAMLError as e:
        raise ConfigError(f"配置文件格式错误: {e}") from e
    if not isinstance(data, dict):
        return []  # 与 custom_instructions 读取口径一致
    return parse_hooks(data)
