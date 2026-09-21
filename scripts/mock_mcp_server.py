"""最小 stdio MCP Server：供测试与 e2e 验收共用的 mock 实现。

提供三个工具：
- echo(text)：原样回显
- add(a, b)：求和
- fail：返回 isError 响应（模拟远端工具失败）

环境变量 MOCK_PAGE_SIZE=N 时，tools/list 按每页 N 个工具分页返回
（nextCursor），供游标翻页测试。

用法：在 MewCode 配置里声明
    mcp_servers:
      demo:
        command: python
        args: [scripts/mock_mcp_server.py]
"""

from __future__ import annotations

import json
import os
import sys

# 全部工具定义（保持 MCP 规范：name 只含字母数字下划线连字符）
TOOLS = [
    {
        "name": "echo",
        "description": "把输入文本原样回显",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "要回显的文本"}},
            "required": ["text"],
        },
    },
    {
        "name": "add",
        "description": "计算两个数字之和",
        "inputSchema": {
            "type": "object",
            "properties": {
                "a": {"type": "number", "description": "第一个加数"},
                "b": {"type": "number", "description": "第二个加数"},
            },
            "required": ["a", "b"],
        },
    },
    {
        "name": "fail",
        "description": "总是失败的演示工具",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

SERVER_INFO = {"name": "mock-mcp-server", "version": "1.0.0"}
SUPPORTED_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18"]

# MOCK_EXTRA_TOOL=1 时额外暴露 upper 工具（多 Server 重名测试用）
_EXTRA_TOOLS = [
    {
        "name": "upper",
        "description": "把输入文本转为大写",
        "inputSchema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]

# MOCK_CONFLICT_TOOL=1 时额外暴露与内置工具同名的 read_file（e2e 重名场景用）
_CONFLICT_TOOL = [
    {
        "name": "read_file",
        "description": "与内置工具同名的冲突工具",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _send(message: dict) -> None:
    """按 MCP stdio 规范输出单行 JSON 并 flush。"""
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _send_response(msg_id: int, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _send_error(msg_id: int, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _handle_initialize(msg: dict) -> None:
    # 与客户端协商：取双方支持的最高版本（mock 只认可支持集内的版本）
    requested = msg.get("params", {}).get("protocolVersion")
    version = requested if requested in SUPPORTED_VERSIONS else "2025-03-26"
    _send_response(
        msg["id"],
        {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        },
    )


def _handle_tools_list(msg: dict) -> None:
    all_tools = TOOLS + (_EXTRA_TOOLS if os.environ.get("MOCK_EXTRA_TOOL") else [])
    if os.environ.get("MOCK_CONFLICT_TOOL"):
        all_tools = all_tools + _CONFLICT_TOOL
    # MOCK_PAGE_SIZE 存在时分页返回，供游标翻页测试
    page_size = os.environ.get("MOCK_PAGE_SIZE")
    if page_size:
        size = int(page_size)
        cursor = msg.get("params", {}).get("cursor")
        start = int(cursor) if cursor else 0
        page = all_tools[start : start + size]
        result: dict = {"tools": page}
        if start + size < len(all_tools):
            result["nextCursor"] = str(start + size)
        _send_response(msg["id"], result)
        return
    _send_response(msg["id"], {"tools": all_tools})


def _handle_tools_call(msg: dict) -> None:
    params = msg.get("params", {})
    name = params.get("name")
    args = params.get("arguments", {})
    if name == "echo":
        _send_response(
            msg["id"], {"content": [{"type": "text", "text": str(args.get("text", ""))}]}
        )
    elif name == "add":
        total = args.get("a", 0) + args.get("b", 0)
        _send_response(
            msg["id"],
            {
                "content": [
                    {"type": "text", "text": f"{args.get('a', 0)} + {args.get('b', 0)} = {total}"}
                ]
            },
        )
    elif name == "fail":
        _send_response(
            msg["id"],
            {"content": [{"type": "text", "text": "mock 故意失败"}], "isError": True},
        )
    elif name == "upper":
        _send_response(
            msg["id"],
            {"content": [{"type": "text", "text": str(args.get("text", "")).upper()}]},
        )
    else:
        _send_error(msg["id"], -32602, f"未知工具: {name}")


def main() -> None:
    """stdio 主循环：逐行读 JSON，按 method 分发。"""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send_error(0, -32700, "解析错误")
            continue
        method = msg.get("method")
        if method == "initialize":
            _handle_initialize(msg)
        elif method == "tools/list":
            _handle_tools_list(msg)
        elif method == "tools/call":
            _handle_tools_call(msg)
        elif "id" in msg and "method" in msg:
            _send_error(msg["id"], -32601, f"方法未找到: {method}")
        # 通知（无 id）与其余消息一律忽略


if __name__ == "__main__":
    main()
