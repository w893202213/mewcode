"""ThinkingIndicator 计时渲染测试（假时钟 + record Console）。

一轮对话的无回显/错误面板行为随 _agent_round 重写，由
tests/test_agent_loop.py 的循环测试与 e2e 场景承接。
"""

from rich.console import Console

from mewcode.tui.app import ThinkingIndicator, _stream_renderable


class FakeClock:
    """可手动推进的时钟：now 属性为当前时刻，调用返回 now。"""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _render_text(renderable) -> str:
    """把 renderable 渲染成纯文本（record Console，导出后清空）。"""
    console = Console(record=True, force_terminal=False)
    console.print(renderable)
    return console.export_text()


def test_indicator_seconds_tick():
    """计时行按经过整秒数跳动，且为灰色样式。"""
    clock = FakeClock()
    indicator = ThinkingIndicator(clock=clock)
    assert "thinking… 0s" in _render_text(indicator)
    clock.advance(3)
    assert "thinking… 3s" in _render_text(indicator)


def test_indicator_style_is_grey():
    """计时行携带 grey50 灰色样式。"""
    clock = FakeClock()
    console = Console(record=True, force_terminal=False)
    segments = console.render(ThinkingIndicator(clock=clock))
    assert any(str(seg.style) == "grey50" for seg in segments)


def test_stream_renderable_silent_period():
    """静默期（无思考内容、无回复）：仅灰色计时行。"""
    indicator = ThinkingIndicator(clock=FakeClock())
    out = _render_text(_stream_renderable("", "", indicator))
    assert "thinking…" in out


def test_stream_renderable_thinking_period():
    """思考期：计时行与思考文本并存。"""
    indicator = ThinkingIndicator(clock=FakeClock())
    out = _render_text(_stream_renderable("让我想想", "", indicator))
    assert "thinking…" in out
    assert "让我想想" in out


def test_stream_renderable_reply_started():
    """回复开始：计时行隐去，只显示正文。"""
    indicator = ThinkingIndicator(clock=FakeClock())
    out = _render_text(_stream_renderable("让我想想", "你好呀", indicator))
    assert "thinking…" not in out
    assert "你好呀" in out
