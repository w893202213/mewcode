"""Hook 动作执行测试（ch15 F5/F6）：command/http/inject/subagent。"""

import logging

import httpx
import pytest

from mewcode.hooks.actions import (
    exec_command,
    exec_http,
    exec_inject,
    exec_subagent,
)
from mewcode.hooks.loader import HookAction

CTX = {"event": "tool_before", "round": 1, "tool": "run_command", "args": {}}


def _command_action(run: str) -> HookAction:
    return HookAction(type="command", run=run)


# ----------------------------------------------------------------------
# command
# ----------------------------------------------------------------------


def test_command_receives_stdin_json():
    run = (
        "python3 -c 'import json,sys; d=json.load(sys.stdin); "
        'print(d["event"] + "|" + d["tool"])\''
    )
    result = exec_command(_command_action(run), CTX, timeout=10)
    assert result.ok is True
    assert result.message == "tool_before|run_command"


def test_command_env_vars():
    result = exec_command(
        _command_action('echo "$MEW_EVENT $MEW_TOOL"'), CTX, timeout=10
    )
    assert result.ok is True
    assert result.message == "tool_before run_command"


def test_command_exit_zero():
    result = exec_command(_command_action("echo hello"), CTX, timeout=10)
    assert result.ok is True
    assert result.message == "hello"
    assert result.deny is False


def test_command_exit_nonzero_denies():
    result = exec_command(
        _command_action("echo 危险参数; exit 1"), CTX, timeout=10
    )
    assert result.ok is False
    assert result.deny is True
    assert result.message == "危险参数"


def test_command_timeout():
    result = exec_command(_command_action("sleep 5"), CTX, timeout=1)
    assert result.ok is False
    assert result.deny is False
    assert "超时" in result.message


# ----------------------------------------------------------------------
# http（MockTransport，不发起真实网络请求）
# ----------------------------------------------------------------------


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _http_action(url: str, method: str = "GET", headers=None, body: str = ""):
    return HookAction(
        type="http", url=url, method=method, headers=headers or {}, body=body
    )


def test_http_2xx_ok():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="OK")

    result = exec_http(
        _http_action("https://example.com"), timeout=5, client=_mock_client(handler)
    )
    assert result.ok is True
    assert result.deny is False
    assert "200" in result.message


def test_http_non_2xx_denies_with_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="NOT FOUND")

    result = exec_http(
        _http_action("https://example.com"), timeout=5, client=_mock_client(handler)
    )
    assert result.ok is True
    assert result.deny is True
    assert result.message == "NOT FOUND"


def test_http_error_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("连接失败")

    result = exec_http(
        _http_action("https://example.com"), timeout=5, client=_mock_client(handler)
    )
    assert result.ok is False
    assert result.deny is False
    assert "连接失败" in result.message


def test_http_forwards_method_headers_body():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["header"] = request.headers.get("X-Test")
        seen["body"] = request.content
        return httpx.Response(200)

    exec_http(
        _http_action(
            "https://example.com/hook",
            method="POST",
            headers={"X-Test": "v1"},
            body='{"k": 1}',
        ),
        timeout=5,
        client=_mock_client(handler),
    )
    assert seen == {"method": "POST", "header": "v1", "body": b'{"k": 1}'}


# ----------------------------------------------------------------------
# inject / subagent
# ----------------------------------------------------------------------


def test_inject_ok_with_prompt():
    result = exec_inject(HookAction(type="inject", prompt="注意安全"))
    assert result.ok is True
    assert result.message == "注意安全"


def test_subagent_placeholder(caplog):
    with caplog.at_level(logging.INFO, logger="mewcode.hooks"):
        result = exec_subagent(HookAction(type="subagent", prompt="检查代码"))
    assert result.ok is True
    assert "占位" in caplog.text
