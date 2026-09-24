"""Hook 条件引擎：字段点路径取值 + 四种模式匹配 + all/any 组合求值（ch15 F3）。

独立实现、不依赖 permissions 包：glob 归一逻辑与权限规则口径一致
（permissions/rules.py 的 `**/`→`*`、`**`→`*`），复制两行并注释说明。
"""

from __future__ import annotations

import fnmatch
import re

from mewcode.hooks.loader import HookCondition

# 点路径取值缺失哨兵：条件字段不存在时该条件不命中（spec F3）
MISSING = object()


def get_path(ctx: dict, field: str) -> object:
    """按 `.` 逐段查 dict；中途非 dict 或键缺失返回 MISSING 哨兵。"""
    current: object = ctx
    for part in field.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def match_pattern(pattern: str, value: str) -> bool:
    """单串四模式判定（spec F3）：

    ① `!` 前缀→剥一层，剩余部分匹配后取反（递归）；
    ② `/` 开头且 `/` 结尾且长度 ≥2→正则（re.fullmatch，加载时已编译校验）；
    ③ 含 `*`/`?`→glob（`**/`→`*`、`**`→`*` 归一，与权限规则口径一致）；
    ④ 其余→精确相等。

    非字符串值先 str() 再匹配（如 result.ok 布尔、args 里嵌套数字）。
    """
    if not isinstance(value, str):
        value = str(value)
    if pattern.startswith("!"):
        return not match_pattern(pattern[1:], value)
    if len(pattern) >= 2 and pattern.startswith("/") and pattern.endswith("/"):
        return re.fullmatch(pattern[1:-1], value) is not None
    if "*" in pattern or "?" in pattern:
        # 与 permissions/rules.py 的 glob 口径一致：`*` 跨路径分隔符匹配
        normalized = pattern.replace("**/", "*").replace("**", "*")
        return fnmatch.fnmatch(value, normalized)
    return pattern == value


def eval_conditions(
    conditions: list[HookCondition], match: str, ctx: dict
) -> bool:
    """条件组合求值：空列表恒 True；any=有真即真、其余（all）=全真才真。

    任一字段缺失（MISSING）该条件视为 False。
    """
    if not conditions:
        return True
    results = [
        False
        if (value := get_path(ctx, cond.field)) is MISSING
        else match_pattern(cond.pattern, value)
        for cond in conditions
    ]
    if match == "any":
        return any(results)
    return all(results)
