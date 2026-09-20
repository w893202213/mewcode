"""StreamCollector 测试：双路收集的累积路径。"""

from mewcode.agent.collector import StreamCollector


def test_text_accumulates_in_order():
    c = StreamCollector()
    c.add_text("你好")
    c.add_text("，")
    c.add_text("世界")
    assert c.full_text == "你好，世界"


def test_thinking_accumulates_independently():
    c = StreamCollector()
    c.add_text("正文")
    c.add_thinking("想想")
    c.add_thinking("再想")
    assert c.full_text == "正文"
    assert c.thinking_text == "想想再想"


def test_thinking_raw_preserved_as_is():
    c = StreamCollector()
    blocks = [{"type": "thinking", "thinking": "x", "signature": "sig-1"}]
    c.set_thinking_raw(blocks)
    assert c.thinking_raw == blocks
    # 外部修改传入列表不影响已保存内容
    blocks.append({"type": "thinking", "thinking": "y", "signature": "sig-2"})
    assert len(c.thinking_raw) == 1


def test_empty_collector():
    c = StreamCollector()
    assert c.full_text == ""
    assert c.thinking_text == ""
    assert c.thinking_raw == []
