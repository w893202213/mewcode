"""Tab 补全：斜杠命令前缀匹配（ch12 F6）。

基于注册中心的可见命令（隐藏命令不参与）+ 别名：单匹配直接补主命令名，
多匹配弹菜单（display_meta 显示描述）；别名命中补主名，不在输入行留别名。
"""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from mewcode.commands.registry import CommandRegistry


class CommandCompleter(Completer):
    """命令名位置的前缀补全（参数位置不补）。"""

    def __init__(self, registry: CommandRegistry) -> None:
        self._registry = registry

    def get_completions(self, document: Document, complete_event: CompleteEvent):
        text = document.text_before_cursor
        # 只补命令名：输入须以 / 开头且光标前无空格（进入参数位置后不补）
        if not text.startswith("/") or " " in text:
            return
        prefix = text[1:].lower()
        matches = [
            spec
            for spec in self._registry.visible()
            if spec.name.startswith(prefix)
            or any(alias.startswith(prefix) for alias in spec.aliases)
        ]
        if not matches:
            return
        start = -len(text)  # 替换整个已输入前缀
        if len(matches) == 1:
            yield Completion("/" + matches[0].name, start_position=start)
            return
        for spec in matches:
            yield Completion(
                "/" + spec.name, start_position=start, display_meta=spec.description
            )
