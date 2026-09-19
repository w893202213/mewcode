"""DoublePressTracker 双击判定测试（假时钟）。"""

from mewcode.tui.double_press import DOUBLE_PRESS_WINDOW, DoublePressTracker


class FakeClock:
    """可手动推进的时钟：now 属性为当前时刻，调用返回 now。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_first_press_starts_hint():
    clock = FakeClock()
    tracker = DoublePressTracker(clock=clock)
    assert tracker.press() is False
    assert tracker.hint_active() is True


def test_second_press_within_window_exits_and_clears():
    clock = FakeClock()
    tracker = DoublePressTracker(clock=clock)
    tracker.press()
    clock.advance(2.9)
    assert tracker.press() is True
    # 退出判定后状态清空，进入全新窗口
    assert tracker.hint_active() is False


def test_press_after_expiry_starts_new_window():
    clock = FakeClock()
    tracker = DoublePressTracker(clock=clock)
    tracker.press()
    clock.advance(DOUBLE_PRESS_WINDOW)  # 恰好 3.0 秒：算过期
    assert tracker.press() is False  # 重新开始计数，不退出
    assert tracker.hint_active() is True
    # 新窗口内第二次才判为退出
    clock.advance(1.0)
    assert tracker.press() is True


def test_reset_clears_state():
    clock = FakeClock()
    tracker = DoublePressTracker(clock=clock)
    tracker.press()
    assert tracker.hint_active() is True
    tracker.reset()
    assert tracker.hint_active() is False
    # reset 后 press 从首次算起
    assert tracker.press() is False


def test_hint_expires_at_window_boundary():
    clock = FakeClock()
    tracker = DoublePressTracker(clock=clock)
    tracker.press()
    clock.advance(DOUBLE_PRESS_WINDOW - 0.01)
    assert tracker.hint_active() is True
    clock.advance(0.01)  # 恰好 3.0 秒：提示过期
    assert tracker.hint_active() is False
