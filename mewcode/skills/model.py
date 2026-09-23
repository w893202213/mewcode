"""技能数据模型：Skill 定义、frontmatter 解析校验、参数占位符替换（ch13 F1）。

技能文件格式（单文件技能 *.md；目录型技能的入口为 */SKILL.md）::

    ---
    name: commit          # 必填：唯一名字（非空、不含空白，可作斜杠命令名）
    description: 提交代码 # 必填：一句话说明
    tools:                # 可选：可见工具白名单；缺省 = 不收窄
      - run_command
    mode: independent     # 可选：shared | independent；缺省 shared
    history: 3            # 可选：独立模式带入主对话最近消息条数；缺省 0
    model: claude-xxx     # 可选：独立模式指定模型
    ---
    （正文：发给模型的 SOP 指令，支持 $ARGS / $ARGUMENTS 占位符）

校验规则：字段类型不对 = 解析失败（返回 None + 原因），由调用方跳过
并警告；未知字段忽略（向前兼容）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml


class SkillMode(Enum):
    """技能执行模式：共享（当前对话）或独立（单独对话 + 摘要回流）。"""

    SHARED = "shared"
    INDEPENDENT = "independent"


@dataclass(frozen=True)
class Skill:
    """一个技能的完整定义（解析产物，不可变）。"""

    name: str  # 唯一名字（frontmatter 权威，与文件名无关）
    description: str  # 一句话说明（启动上下文与 /help 展示）
    body: str  # SOP 正文（未替换占位符）
    tools: tuple[str, ...] | None  # 工具白名单；None = 不收窄
    mode: SkillMode  # 缺省 SHARED
    history: int  # 独立模式带入主对话消息条数；缺省 0
    model: str | None  # 独立模式指定模型
    dir: Path | None  # 目录型技能的目录路径（SOP 引用资源用）；单文件为 None
    source: Path  # 文件路径（热更新重读用）
    level: str  # 来源层级 project / user / builtin（报错展示用）


def parse_skill_file(
    path: Path, level: str, dir: Path | None = None
) -> tuple[Skill | None, str | None]:
    """解析单个技能文件；成功返回 (Skill, None)，失败返回 (None, 原因)。

    目录型技能由调用方（discover）传入 dir（入口文件所在目录）；
    level 为来源层级字符串。本函数不抛异常。
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, str(e)

    meta, error = _parse_frontmatter(content)
    if error is not None:
        return None, error
    body = content.split("---", 2)[2].strip()
    return (
        Skill(
            name=meta["name"],
            description=meta["description"],
            body=body,
            tools=meta["tools"],
            mode=meta["mode"],
            history=meta["history"],
            model=meta["model"],
            dir=dir,
            source=path,
            level=level,
        ),
        None,
    )


def _parse_frontmatter(content: str) -> tuple[dict, str | None]:
    """提取并校验 frontmatter；失败返回 ({}, 原因)。"""
    if not content.startswith("---"):
        return {}, "缺少 frontmatter（首行须为 ---）"
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, "frontmatter 未闭合（缺少结尾 ---）"
    raw = parts[1]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return {}, f"frontmatter YAML 解析失败: {e}"
    if not isinstance(data, dict):
        return {}, "frontmatter 必须是键值映射"

    # name：必填、字符串、非空、不含空白（要能当斜杠命令名）
    name = data.get("name")
    if name is None:
        return {}, "缺少必填字段: name"
    if not isinstance(name, str) or not name.strip():
        return {}, "字段 name 必须是非空字符串"
    name = name.strip()
    if any(c.isspace() for c in name):
        return {}, f"字段 name 含空白: {name!r}"

    # description：必填、非空字符串
    description = data.get("description")
    if description is None:
        return {}, "缺少必填字段: description"
    if not isinstance(description, str) or not description.strip():
        return {}, "字段 description 必须是非空字符串"
    description = description.strip()

    # tools：None 或字符串列表（元素非空）
    tools = data.get("tools")
    if tools is not None:
        if not isinstance(tools, list) or not all(
            isinstance(t, str) and t.strip() for t in tools
        ):
            return {}, "字段 tools 必须是字符串列表"
        tools = tuple(t.strip() for t in tools)

    # mode：缺省 shared；仅接受 shared / independent
    mode_raw = data.get("mode", "shared")
    if not isinstance(mode_raw, str) or mode_raw not in ("shared", "independent"):
        return {}, f"字段 mode 非法: {mode_raw!r}（可选 shared / independent）"
    mode = SkillMode(mode_raw)

    # history：缺省 0；须非负整数（排除 bool：isinstance(True, int) 为真）
    history = data.get("history", 0)
    if isinstance(history, bool) or not isinstance(history, int) or history < 0:
        return {}, f"字段 history 必须是非负整数: {history!r}"
    history = int(history)

    # model：None 或非空字符串
    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        return {}, "字段 model 必须是非空字符串"

    return (
        {
            "name": name,
            "description": description,
            "tools": tools,
            "mode": mode,
            "history": history,
            "model": model,
        },
        None,
    )


def substitute_args(body: str, args: str) -> str:
    """把正文中的 $ARGS / $ARGUMENTS 占位符替换为传入内容（先长后短防前缀误伤）。"""
    return body.replace("$ARGUMENTS", args).replace("$ARGS", args)
