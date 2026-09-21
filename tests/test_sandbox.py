"""路径沙箱测试：项目内放行、各类逃逸拒绝。"""

import os

import pytest

from mewcode.permissions.sandbox import check_inside


@pytest.fixture
def root(tmp_path):
    """已 resolve 的项目根（与 gate 装配时一致）。"""
    return tmp_path.resolve()


def test_inside_relative(root):
    assert check_inside(root, "src/main.py") == (True, "")


def test_inside_nested(root):
    assert check_inside(root, "a/b/c.txt") == (True, "")


def test_inside_absolute(root):
    assert check_inside(root, str(root / "x.py")) == (True, "")


def test_outside_parent(root):
    ok, reason = check_inside(root, "../outside.txt")
    assert not ok and "越界" in reason


def test_outside_deep_parent(root):
    ok, reason = check_inside(root, "../../etc/passwd")
    assert not ok and "越界" in reason


def test_outside_absolute(root):
    ok, reason = check_inside(root, "/etc/passwd")
    assert not ok and "越界" in reason


def test_empty_path(root):
    ok, reason = check_inside(root, "  ")
    assert not ok and "为空" in reason


def test_symlink_pointing_outside(root, tmp_path):
    outside_dir = tmp_path.parent / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    (outside_dir / "secret.txt").write_text("secret", encoding="utf-8")
    link = root / "link"
    try:
        link.symlink_to(outside_dir)
    except OSError:
        pytest.skip("环境不支持符号链接")
    ok, reason = check_inside(root, "link/secret.txt")
    assert not ok and "越界" in reason


def test_symlink_pointing_inside(root):
    inner = root / "real"
    inner.mkdir(exist_ok=True)
    link = root / "link"
    try:
        link.symlink_to(inner)
    except OSError:
        pytest.skip("环境不支持符号链接")
    assert check_inside(root, "link/x.txt") == (True, "")


def test_symlink_chain_escape(root, tmp_path):
    """多级符号链接链最终指向项目外也拦。"""
    outside_dir = tmp_path.parent / "outside_chain"
    outside_dir.mkdir(exist_ok=True)
    (root / "mid").mkdir(exist_ok=True)
    try:
        (root / "mid").joinpath("hop").symlink_to(outside_dir)
        (root / "first").symlink_to(root / "mid")
    except OSError:
        pytest.skip("环境不支持符号链接")
    ok, reason = check_inside(root, "first/hop/x.txt")
    assert not ok and "越界" in reason


def test_nonexistent_target_uses_parent(root):
    """目标不存在（write_file 新建）：按解析后的父路径判定。"""
    assert check_inside(root, "new_dir/new_file.txt") == (True, "")
    ok, reason = check_inside(root, "../new_file.txt")
    assert not ok and "越界" in reason


def test_root_itself(root):
    assert check_inside(root, ".") == (True, "")
    assert check_inside(root, "") == (False, "路径为空")
