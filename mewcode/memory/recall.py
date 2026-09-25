"""记忆召回工具（ch17 渐进式披露·方案 A）：模型按需取细节层笔记全文。

模型调用 recall_memory(query) → 读两级 index.md 摘要行 → 同步调 LLM
判相关（固定系统提示，最多选 3 条）→ 读对应 <slug>.md 全文、标注来源
返回。解析失败/无相关/文件缺失一律「没有找到相关记忆」成功结果——
召回是增强手段，不该阻断对话（spec F2）。

构造注入 provider 与两级记忆目录（UseSkillTool 同款模式）：不依赖
ParentContext，子 Agent 线程同样可用（spec F4）；provider 每次调用
新建 httpx.Client（同 NoteUpdater），并发安全。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from mewcode.memory.notes import NoteManager
from mewcode.memory.prompts import (
    RECALL_SYSTEM_PROMPT,
    RecallParseError,
    build_recall_request,
    parse_recall_response,
)
from mewcode.provider.base import Provider, ProviderError, SystemPrompt, TextDelta
from mewcode.session import Message
from mewcode.tools.base import Tool, ToolResult

# 合法 slug（与 notes._slugify 同规则）：读取前校验，防御 LLM 输出
# 路径穿越（"../.."）或意外字符拼进文件路径
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class RecallMemoryTool(Tool):
    """按自然语言 query 召回最相关的长期记忆笔记全文。"""

    name: ClassVar[str] = "recall_memory"
    description: ClassVar[str] = (
        "查长期记忆。需要了解用户偏好、项目约定、过往经验或历史决策时"
        "调用：传入自然语言 query（描述要查的内容），返回最相关记忆笔记"
        "的全文（最多 3 条，含用户级/项目级来源标注）。查不到相关记忆时"
        "返回「没有找到相关记忆」。"
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "自然语言检索描述（如「上次记录的仓库卫生问题」）",
            },
        },
        "required": ["query"],
    }
    read_only: ClassVar[bool] = True  # 只读：不修改记忆文件，计划模式可用

    def __init__(self, provider: Provider, user_dir: Path, project_dir: Path) -> None:
        self._provider = provider
        self._user = NoteManager(user_dir)
        self._project = NoteManager(project_dir)

    def execute(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.failure("query 必须是非空的检索描述")
        user_index = self._user.index_text()
        project_index = self._project.index_text()
        # 两份索引皆空：没有记忆可召回，不发无意义的 LLM 调用
        if not user_index.strip() and not project_index.strip():
            return ToolResult.success("没有找到相关记忆")
        try:
            hits = parse_recall_response(
                self._collect(query, user_index, project_index)
            )
        except RecallParseError:
            # 判定结果无法解析：按「无相关」处理，不阻断对话（spec F2）
            return ToolResult.success("没有找到相关记忆")
        except ProviderError as e:
            # LLM 调用失败：带中文原因的错误结果，主流程不受影响（spec N2）
            return ToolResult.failure(f"记忆检索失败: {e}")
        if not hits:
            return ToolResult.success("没有找到相关记忆")
        sections: list[str] = []
        for hit in hits:
            manager = self._user if hit.scope == "user" else self._project
            text = self._read_note(manager, hit.slug)
            if text is None:
                continue  # 文件缺失（LLM 幻觉/已删除）：跳过不报错
            level = "用户级" if hit.scope == "user" else "项目级"
            sections.append(f"## 笔记: {hit.slug}（{level}记忆）\n{text}")
        if not sections:
            return ToolResult.success("没有找到相关记忆")
        return ToolResult.success("\n\n".join(sections))

    def _read_note(self, manager: NoteManager, slug: str) -> str | None:
        """读一条笔记全文；slug 不合法或文件缺失返回 None（调用方跳过）。"""
        if not _SLUG_RE.match(slug):
            return None
        try:
            return (manager.directory / f"{slug}.md").read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return None

    def _collect(self, query: str, user_index: str, project_index: str) -> str:
        """同步调 LLM 判相关，收集完整回复文本（同 NoteUpdater._call_llm 模式）。"""
        request = build_recall_request(query, user_index, project_index)
        system = SystemPrompt(stable=RECALL_SYSTEM_PROMPT, dynamic="")
        parts: list[str] = []
        for event in self._provider.stream(
            [Message(role="user", text=request)], [], system
        ):
            if isinstance(event, TextDelta):
                parts.append(event.text)
        return "".join(parts)
