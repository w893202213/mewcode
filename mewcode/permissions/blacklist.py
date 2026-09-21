"""危险命令黑名单：执行前硬拦截，唯一不可被配置/规则/模式放开的层。

只查 run_command 的 command 参数；命中即拒绝执行。正则列表内置，
全部有单元测试正反例（tests/test_blacklist.py）。
"""

from __future__ import annotations

import re

# (编译后正则, 中文描述)。描述用于拒绝反馈，让模型知道踩了哪条。
BLACKLIST: list[tuple[re.Pattern, str]] = [
    # fork bomb：`:(){ :|:& };:`（容忍任意空白，含带空格变体 `: () { : | : & } ; :`）
    (
        re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
        "fork bomb（fork 炸弹）",
    ),
    # 格式化文件系统
    (re.compile(r"\bmkfs(\.\w+)?\b", re.IGNORECASE), "mkfs 格式化文件系统"),
    # dd 直写设备
    (re.compile(r"\bdd\b", re.IGNORECASE), "dd 直写设备"),
    # 提权
    (re.compile(r"\bsudo\b", re.IGNORECASE), "sudo 提权"),
    # rm 递归/强制删除根目录（`rm -rf /`、`rm -rf /*`；`rm -rf build/` 不拦）
    (
        re.compile(
            r"\brm\b[^\n|;]*\s+(-[a-zA-Z]*[rf][a-zA-Z]*|--recursive|--force)"
            r"[^\n|;]*\s+/(\s|$|\*)",
            re.IGNORECASE,
        ),
        "rm -rf 删除根目录",
    ),
    # 下载即执行（curl/wget 管道到 shell）
    (
        re.compile(
            r"\b(curl|wget)\b[^\n|;]*\|[^\n|;]*\b(ba|z|fi)?sh\b",
            re.IGNORECASE,
        ),
        "下载内容直接管道到 shell 执行",
    ),
    # 关机/重启
    (
        re.compile(r"\b(shutdown|reboot|poweroff|halt)\b", re.IGNORECASE),
        "关机/重启命令",
    ),
    # 重定向覆写块设备
    (
        re.compile(r">>?\s*/dev/(sd[a-z]*|hd[a-z]*|nvme\d\w*|mapper/\S+)", re.IGNORECASE),
        "重定向覆写块设备",
    ),
    # 递归改根目录权限（标志与路径之间可带参数，如 chmod -R 777 /）
    (
        re.compile(
            r"\bchmod\b[^\n|;]*\s+(-R|--recursive)\b[^\n|;]*\s+/",
            re.IGNORECASE,
        ),
        "递归修改根目录权限",
    ),
]


def find_hit(command: str) -> str | None:
    """检查命令是否命中黑名单；命中返回中文描述，未命中返回 None。"""
    text = command.strip()
    if not text:
        return None
    for pattern, description in BLACKLIST:
        if pattern.search(text):
            return description
    return None
