"""系统提示拼装：环境信息、会话级开关指令、补充块与最终 SystemPrompt。

拼装全程为纯函数过程（不依赖网络与界面）：
- stable = 七个固定模块文本（modules.STABLE_TEXT），进入可缓存前缀；
- dynamic = <supplement> 补充块（环境信息现取日期 + 开关指令文本）
  +【自定义指令】块（配置了才有），紧跟缓存断点之后、不缓存。

补充块带特殊标签（<supplement>），走系统通道——模型不会把它当成
用户输入来回复，也不会写进会话历史。
"""

from __future__ import annotations

import datetime
from collections.abc import Callable
from dataclasses import dataclass

from mewcode.provider.base import SystemPrompt
from mewcode.prompt.modules import STABLE_TEXT


@dataclass
class Environment:
    """环境信息快照：启动时采样 cwd 与平台，日期在每次构建补充块时现取。

    cwd 与 shell 工具的启动目录语义一致；采样失败（极端情况）时用
    「未知」占位，不中断启动。
    """

    cwd: str  # MewCode 启动目录（绝对路径）
    platform: str  # 如 "Linux 6.6.87.2-microsoft-standard-WSL2"


@dataclass
class SwitchDirective:
    """一个会话级开关的指令与注入节奏。

    注入节奏：首轮完整、每隔 repeat_every 轮重复完整、其余轮精简，
    控制指令占用的 token 量。当前唯一的开关是 Plan Mode。
    """

    name: str  # 开关名（如 "plan_mode"，用于展示与排错）
    full_text: str  # 完整版（首轮与周期轮注入）
    slim_text: str  # 精简版（其余轮注入）
    repeat_every: int = 4  # 每隔 N 轮重复完整版

    def text_for(self, iteration: int) -> str:
        """第 iteration 轮注入哪一版：首轮或 iteration % repeat_every == 0 → 完整，否则精简。"""
        if iteration == 1 or iteration % self.repeat_every == 0:
            return self.full_text
        return self.slim_text


def build_supplement(
    env: Environment, directive_texts: list[str], date: datetime.date | None = None
) -> str:
    """生成 <supplement> 补充块：环境信息小节 + 会话状态小节（指令非空时）。

    date 缺省现取当天日期（可注入便于测试跨天与固定日期断言）。
    """
    today = (date or datetime.date.today()).isoformat()
    lines = [
        "<supplement>",
        "【环境信息】",
        f"- 工作目录: {env.cwd}",
        f"- 操作系统: {env.platform}",
        f"- 当前日期: {today}",
    ]
    if directive_texts:
        lines.append("")
        lines.append("【会话状态】")
        lines.extend(directive_texts)
    lines.append("</supplement>")
    return "\n".join(lines)


def build_custom_block(custom_instructions: str) -> str:
    """自定义指令块；空内容返回空串（该模块不参与拼装）。"""
    text = custom_instructions.strip()
    if not text:
        return ""
    return f"【自定义指令】\n{text}"


def build_memory_block(memory_text: str) -> str:
    """记忆块；空内容返回空串（ch11 F5）。"""
    text = memory_text.strip()
    if not text:
        return ""
    return f"【记忆】\n{text}"


class SystemPromptBuilder:
    """拼装一次请求的系统提示。构造时固定 env 与自定义指令；每次 build 现取日期。

    memory_loader（ch11）：可选的回调，每次 build 调用以现取记忆索引文本——
    本轮新产生的笔记下一轮即可见；索引变化只影响 dynamic，不打断 stable 缓存。
    """

    def __init__(
        self,
        env: Environment,
        custom_instructions: str = "",
        memory_loader: Callable[[], str] | None = None,
    ) -> None:
        self._env = env
        self._custom_block = build_custom_block(custom_instructions)
        self._memory_loader = memory_loader

    def build(self, directives: list[SwitchDirective], iteration: int) -> SystemPrompt:
        """返回 SystemPrompt：stable=七模块；dynamic=补充块+记忆块+自定义指令块。"""
        directive_texts = [d.text_for(iteration) for d in directives]
        supplement = build_supplement(self._env, directive_texts)
        memory_block = (
            build_memory_block(self._memory_loader())
            if self._memory_loader is not None
            else ""
        )
        dynamic = "\n\n".join(
            p for p in (supplement, memory_block, self._custom_block) if p
        )
        return SystemPrompt(stable=STABLE_TEXT, dynamic=dynamic)
