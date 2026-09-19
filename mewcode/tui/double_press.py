"""双击 Ctrl+C 退出：双击判定纯逻辑，供 TUI 主循环使用。

无 TUI 依赖，时钟可注入，便于用假时钟做单元测试。
"""

from __future__ import annotations

import time
from enum import Enum, auto
from typing import Callable

# 双击窗口（秒）：两次 Ctrl+C 间隔小于该值算「双击退出」，固定值不做配置
DOUBLE_PRESS_WINDOW = 3.0


class ExitSignal(Enum):
    """prompt() 的哨兵返回值：区别于普通文本输入。"""

    EXIT = auto()


class DoublePressTracker:
    """双击 Ctrl+C 判定：第一次按下开始计时，窗口内第二次按下判为退出。

    时钟默认 time.monotonic（满足 N2：不受系统时间调整影响），
    单测可注入假时钟。
    """

    def __init__(
        self,
        window: float = DOUBLE_PRESS_WINDOW,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window = window
        self._clock = clock
        self._first_press_at: float | None = None

    def press(self) -> bool:
        """记录一次 Ctrl+C。窗口内第二次按下返回 True（并清空状态）；
        首次按下（或已过期后的按下）返回 False。
        """
        now = self._clock()
        if self._first_press_at is None or now - self._first_press_at >= self._window:
            # 首次按下或窗口已过期：重新开始计数
            self._first_press_at = now
            return False
        # 窗口内第二次按下：判为退出，清空状态
        self._first_press_at = None
        return True

    def reset(self) -> None:
        """清空计数状态。流式中断后调用，保证计数从头开始。"""
        self._first_press_at = None

    def hint_active(self) -> bool:
        """是否处于提示窗口内（供 bottom_toolbar 渲染判断）。"""
        return (
            self._first_press_at is not None
            and self._clock() - self._first_press_at < self._window
        )
