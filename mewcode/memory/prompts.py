"""记忆系统的 LLM 提示模板与响应解析（ch11 F5 + ch17 召回）。

笔记更新：LLM 阅读最近对话、现有笔记与索引，输出笔记变更列表
（add/update/none，去重由 LLM 判断）与更新后的两份索引全文。
召回判相关（ch17）：LLM 依据 query 与两份索引摘要行，选出最相关
笔记（最多 3 条），只输出选中条目的 scope + slug。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from mewcode.session import Message

# 注意：NOTE_TYPES / NoteChange 在函数内惰性导入——notes 模块反向
# import 本模块的模板（循环依赖），包级导入会触发部分初始化错误。


class NotesParseError(Exception):
    """笔记更新响应解析失败（整体 JSON 非法），消息中文可读。"""


class RecallParseError(Exception):
    """召回判相关响应解析失败（整体 JSON 非法），消息中文可读。"""


@dataclass
class RecallHit:
    """召回判相关选中的一条笔记（ch17）。"""

    scope: str  # "user" | "project"（解析时校验，非法值丢弃）
    slug: str  # 笔记文件名（不含 .md）


NOTES_SYSTEM_PROMPT = """你是 MewCode 的记忆维护器。阅读用户与助手最近的对话，维护长期记忆笔记。

笔记分四类（type 字段）：
- user_preference 用户偏好：用户的习惯、喜好、工作风格（如「偏好中文回答」）
- correction 纠正反馈：用户对助手行为的纠正或批评（如「不要问那么多问题」）
- project_knowledge 项目知识：关于当前项目的技术栈、架构、约定
- reference 参考资料：值得记住的外部资源（文档链接、命令用法）

笔记分两级（scope 字段）：
- user：跨项目通用，与具体项目无关
- project：只对当前项目有效

规则：
1. 去重：与现有笔记同主题的信息更新既有笔记（action=update）而非新增；name 不变、content 与 description 写更新后的完整内容。
2. 无新信息时输出空 changes 列表；不要把对话中无关紧要的内容记成笔记。
3. 索引规则：每行一条 `- [name](name.md) — description`，覆盖该级全部笔记；保持 ≤100 行且 ≤12KB，超限时精简（合并或删除最不重要的条目）。
4. 只输出一个 JSON 对象，不要输出任何其他文本。

输出格式：
{"changes": [{"action": "add|update|none", "type": "<四类之一>", "scope": "user|project", "name": "<小写英文slug>", "description": "<一句话>", "content": "<笔记全文>"}], "user_index": "<用户级新索引全文>", "project_index": "<项目级新索引全文>"}"""


RECALL_SYSTEM_PROMPT = """你是 MewCode 的记忆召回器。给定用户的查询与两级记忆索引（每行一条 `- [name](name.md) — description`，description 是笔记的一句话摘要），选出与查询最相关的笔记。

规则：
1. 只从给定的索引中挑选，不要编造不存在的笔记。
2. 最多选 3 条；没有相关的输出空列表。
3. 只输出一个 JSON 对象，不要输出任何其他文本。

输出格式：
{"notes": [{"scope": "user|project", "slug": "<索引行中的 name>"}]}"""


def build_recall_request(query: str, user_index: str, project_index: str) -> str:
    """组装召回判相关的 user 消息：查询 + 两份索引摘要行。"""
    return (
        "【查询】\n"
        f"{query}\n\n"
        "【用户级索引】\n"
        f"{user_index or '（无）'}\n\n"
        "【项目级索引】\n"
        f"{project_index or '（无）'}\n\n"
        "请按系统提示选出与查询最相关的笔记，只输出 JSON。"
    )


def parse_recall_response(text: str) -> list[RecallHit]:
    """解析召回判相关响应 → 选中列表（≤3 条、去重）；整体非法抛 RecallParseError。"""
    data = _extract_json(text, RecallParseError)
    if not isinstance(data, dict):
        raise RecallParseError("响应不是 JSON 对象")
    hits: list[RecallHit] = []
    seen: set[tuple[str, str]] = set()
    raw_notes = data.get("notes")
    if isinstance(raw_notes, list):
        for item in raw_notes:
            if not isinstance(item, dict):
                continue
            scope = item.get("scope")
            slug = item.get("slug")
            if scope not in ("user", "project"):
                continue
            if not isinstance(slug, str) or not slug:
                continue
            slug = slug.strip()
            # LLM 可能把索引行的 `(name.md)` 整段抄成 slug：剥掉后缀
            if slug.endswith(".md"):
                slug = slug[:-3]
            if not slug:
                continue
            key = (scope, slug)
            if key in seen:
                continue
            seen.add(key)
            hits.append(RecallHit(scope=scope, slug=slug))
            if len(hits) >= 3:
                break
    return hits


def build_notes_request(
    recent: list[Message],
    user_index: str,
    project_index: str,
    user_notes: str,
    project_notes: str,
) -> str:
    """组装 user 消息：最近对话 + 现有笔记 + 两份索引。"""
    dialogue = "\n".join(
        f"{m.role}: {m.text}" if m.text else f"{m.role}: [工具调用/结果]"
        for m in recent
    )
    return (
        "【最近对话】\n"
        f"{dialogue}\n\n"
        "【用户级现有笔记】\n"
        f"{user_notes or '（无）'}\n\n"
        "【项目级现有笔记】\n"
        f"{project_notes or '（无）'}\n\n"
        "【用户级索引】\n"
        f"{user_index or '（无）'}\n\n"
        "【项目级索引】\n"
        f"{project_index or '（无）'}\n\n"
        "请按系统提示维护记忆，只输出 JSON。"
    )


def parse_notes_response(text: str) -> tuple[list[NoteChange], str, str]:
    """解析 LLM 响应 → (changes, user_index, project_index)；整体非法抛 NotesParseError。"""
    data = _extract_json(text)
    if not isinstance(data, dict):
        raise NotesParseError("响应不是 JSON 对象")
    changes: list[NoteChange] = []
    raw_changes = data.get("changes")
    if isinstance(raw_changes, list):
        for item in raw_changes:
            change = _parse_change(item)
            if change is not None:
                changes.append(change)
    user_index = data.get("user_index")
    project_index = data.get("project_index")
    return (
        changes,
        user_index if isinstance(user_index, str) else "",
        project_index if isinstance(project_index, str) else "",
    )


def _extract_json(text: str, error_cls: type[Exception] = NotesParseError):
    """提取 JSON：直接解析失败则剥离 ```json 围栏重试；仍失败抛 error_cls。

    error_cls 参数（ch17）：召回解析传 RecallParseError，笔记更新缺省 NotesParseError。
    """
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, TypeError) as e:
            raise error_cls(f"JSON 解析失败: {e}") from e
    raise error_cls("响应不含合法 JSON")


def _parse_change(item) -> NoteChange | None:
    """校验单个 change 条目；字段缺失或取值非法返回 None（丢弃该条）。"""
    from mewcode.memory.notes import NOTE_TYPES, NoteChange  # 惰性导入防循环

    if not isinstance(item, dict):
        return None
    action = item.get("action")
    note_type = item.get("type")
    scope = item.get("scope")
    name = item.get("name")
    description = item.get("description")
    content = item.get("content")
    if action not in ("add", "update", "none"):
        return None
    if note_type not in NOTE_TYPES:
        return None
    if scope not in ("user", "project"):
        return None
    if not isinstance(name, str) or not name:
        return None
    if not isinstance(description, str):
        description = ""
    if not isinstance(content, str):
        content = ""
    return NoteChange(
        action=action,
        note_type=note_type,
        scope=scope,
        name=name,
        description=description,
        content=content,
    )
