"""ThinkingIndicator 计时渲染与 _chat_round 无回显测试（假时钟 + record Console）。"""

from rich.console import Console

from mewcode.agent import ToolRunner
from mewcode.provider.base import Done, ProviderError, TextDelta, ToolCall
from mewcode.session import ChatSession
from mewcode.tools import create_registry
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

    def stream(self, messages, tools=None):
        yield TextDelta("你好")
        yield Done()


def test_chat_round_no_user_echo():
    """一轮对话不再回显用户原文：输出不含用户问题与「▸ 你」前缀，含助手回复。"""
    console = Console(record=True, force_terminal=False)
    session = ChatSession()
    registry = create_registry()
    runner = ToolRunner(registry, session)
    _chat_round(
        console, FakeProvider(), session, registry, runner, "我的问题", DoublePressTracker()
    )
    out = console.export_text()
    assert "我的问题" not in out
    assert "▸ 你" not in out
    assert "你好" in out
    # 会话记录不受显示层影响
    assert session.messages[0].text == "我的问题"
    assert session.messages[1].text == "你好"


class TwoToolsProvider:
    """单响应含两个工具调用的假 Provider：验证 F8 只执行第一个。"""

    def stream(self, messages, tools=None):
        yield ToolCall(id="toolu_1", name="read_file", args={"file_path": "目标.py"})
        yield ToolCall(id="toolu_2", name="grep_code", args={"pattern": "x"})
        yield Done()


def test_chat_round_only_first_tool_call_executed(tmp_path):
    """多工具调用：只执行第一个，其余提示忽略；会话只记录第一个（F8）。"""
    target = tmp_path / "目标.py"
    target.write_text("x = 1\n", encoding="utf-8")
    console = Console(record=True, force_terminal=False)
    session = ChatSession()
    registry = create_registry()
    runner = ToolRunner(registry, session)
    _chat_round(
        console,
        TwoToolsProvider(),
        session,
        registry,
        runner,
        "帮我改",
        DoublePressTracker(),
    )
    out = console.export_text()
    assert "read_file" in out
    assert "忽略" in out  # 第二个工具调用被提示忽略
    assert "grep_code" not in out  # 第二个工具未执行也未展示
    # 会话记录：assistant 只带第一个 tool_use，结果回灌一条
    assistant = session.messages[1]
    assert [u.name for u in assistant.tool_uses] == ["read_file"]
    results = session.messages[2].tool_results
    assert len(results) == 1
    assert results[0].tool_use_id == "toolu_1"


class ErrorProvider:
    """流中途抛错的假 Provider：用于验证错误面板上方无计时残留。"""

    def stream(self, messages, tools=None):
        raise ProviderError("服务端返回错误: mock 服务过载")


def test_chat_round_error_no_timer_leftover():
    """错误路径：显示请求失败面板，计时行不残留。"""
    console = Console(record=True, force_terminal=False)
    session = ChatSession()
    registry = create_registry()
    runner = ToolRunner(registry, session)
    _chat_round(
        console, ErrorProvider(), session, registry, runner, "测试错误", DoublePressTracker()
    )
    out = console.export_text()
    assert "请求失败" in out
    assert "服务端返回错误: mock 服务过载" in out
    assert "thinking…" not in out
    # 本轮 user 消息回滚，不留在上下文
    assert session.messages == []
