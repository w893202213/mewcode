"""ThinkingIndicator 计时渲染与 _chat_round 无回显测试（假时钟 + record Console）。"""

from rich.console import Console

from mewcode.provider.base import Done, TextDelta
from mewcode.session import ChatSession
from mewcode.tui.app import ThinkingIndicator, _chat_round, _stream_renderable
from mewcode.tui.double_press import DoublePressTracker


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


class FakeProvider:
    """极简假 Provider：直接产出正文增量与结束事件。"""

    def stream(self, messages):
        yield TextDelta("你好")
        yield Done()


def test_chat_round_no_user_echo():
    """一轮对话不再回显用户原文：输出不含用户问题与「▸ 你」前缀，含助手回复。"""
    console = Console(record=True, force_terminal=False)
    session = ChatSession()
    _chat_round(console, FakeProvider(), session, "我的问题", DoublePressTracker())
    out = console.export_text()
    assert "我的问题" not in out
    assert "▸ 你" not in out
    assert "你好" in out
    # 会话记录不受显示层影响
    assert session.messages[0].text == "我的问题"
    assert session.messages[1].text == "你好"
