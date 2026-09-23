"""skills 包：技能系统（ch13）——可复用 AI 操作的封装与两阶段加载。

四个模块分工：
- model.py：Skill 数据模型、frontmatter 解析校验、参数占位符替换
- discover.py：三级扫描（项目 > 用户 > 内置）、优先级覆盖、白名单启动校验
- manager.py：SkillManager——激活状态、钉住块快照、白名单并集、斜杠命令注册
- use_skill.py：UseSkillTool——系统级加载工具

内置样板技能（commit/review/test）存放在本目录：单文件技能为 *.md，
目录型技能为 */SKILL.md。
"""

from __future__ import annotations

from pathlib import Path

# 内置技能目录（包内随发行版分发；样板：commit.md、review.md、test/）
BUILTIN_SKILLS_DIR = Path(__file__).resolve().parent

from mewcode.skills.model import (  # noqa: E402
    Skill,
    SkillMode,
    parse_skill_file,
    substitute_args,
)

__all__ = [
    "BUILTIN_SKILLS_DIR",
    "Skill",
    "SkillMode",
    "parse_skill_file",
    "substitute_args",
]
