"""路径沙箱：五个文件工具的路径硬边界（项目根内）。

判定前先 resolve 全链符号链接（含中间路径组件）再做前缀判断，
防 `../`、绝对路径与符号链接逃逸；目标不存在时按已存在部分解析
（resolve(strict=False) 天然覆盖「解析父路径」）。硬边界：规则与
模式均开不了例外。
"""

from __future__ import annotations

from pathlib import Path


def check_inside(root: Path, path: str) -> tuple[bool, str]:
    """判断路径是否落在项目根内；返回 (是否在根内, 中文原因)。

    root 应为已 resolve 的项目根；相对路径以 root 为基准拼接。
    """
    if not path.strip():
        return False, "路径为空"
    target = Path(path)
    if not target.is_absolute():
        target = root / path
    try:
        resolved = target.resolve(strict=False)
    except OSError as e:
        return False, f"路径解析失败: {e}"
    if resolved.is_relative_to(root):
        return True, ""
    return False, f"路径越界: {resolved} 不在项目目录 {root} 内"
