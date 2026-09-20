"""CLI 入口：解析参数、装配组件、启动 TUI。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mewcode.config import ConfigError, load_config, select_profile
from mewcode.provider import create_provider
from mewcode.tools import create_registry
from mewcode.tui.app import run

# 配置文件默认路径
DEFAULT_CONFIG_PATH = Path.home() / ".mewcode" / "config.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mewcode",
        description="MewCode —— 终端 AI 编程助手",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="使用哪份供应商配置（缺省用配置文件中的第一份）",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help=f"配置文件路径（缺省 {DEFAULT_CONFIG_PATH}）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """入口：配置错误打印到 stderr 并以非零码退出。"""
    args = _parse_args(argv)
    try:
        configs = load_config(args.config)
        config = select_profile(configs, args.profile)
    except ConfigError as e:
        print(f"配置错误: {e}", file=sys.stderr)
        sys.exit(1)

    provider = create_provider(config)
    registry = create_registry()
    run(provider, config, registry)
