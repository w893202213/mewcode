"""MCP 配置层测试：解析、${VAR} 展开、两层合并。"""

import pytest
import yaml

from mewcode.config import ConfigError
from mewcode.mcp.config import McpServerConfig, expand_var, load_mcp_servers


def _write(path, data: object) -> None:
    """把 data 写成 YAML 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f)


# 一份完整 stdio 配置
STDIO = {
    "command": "python",
    "args": ["mock_mcp_server.py"],
    "env": {"TOKEN": "${TOKEN}"},
}
# 一份完整 http 配置
HTTP = {
    "url": "https://example.com/mcp",
    "headers": {"Authorization": "Bearer ${TOKEN}"},
}


def _load(tmp_path, user: object, project: object) -> list[McpServerConfig]:
    """把 user/project 两层写成文件后加载（None 表示不写该层）。"""
    user_path = tmp_path / "user.yaml"
    project_path = tmp_path / "project.yaml"
    if user is not None:
        _write(user_path, user)
    if project is not None:
        _write(project_path, project)
    return load_mcp_servers(user_path, project_path)


def test_expand_var(monkeypatch):
    monkeypatch.setenv("MCP_TOKEN", "secret-123")
    assert expand_var("Bearer ${MCP_TOKEN}") == "Bearer secret-123"
    # 无变量的字符串原样返回
    assert expand_var("plain") == "plain"


def test_expand_var_undefined_raises(monkeypatch):
    monkeypatch.delenv("MCP_NO_SUCH_VAR", raising=False)
    with pytest.raises(ConfigError, match="MCP_NO_SUCH_VAR"):
        expand_var("${MCP_NO_SUCH_VAR}")


def test_parse_stdio(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "abc")
    configs = _load(tmp_path, {"mcp_servers": {"demo": STDIO}}, None)
    assert len(configs) == 1
    c = configs[0]
    assert c.name == "demo"
    assert c.transport == "stdio"
    assert c.command == "python"
    assert c.args == ["mock_mcp_server.py"]
    assert c.env == {"TOKEN": "abc"}
    assert c.url is None


def test_parse_http(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKEN", "abc")
    configs = _load(tmp_path, {"mcp_servers": {"remote": HTTP}}, None)
    assert len(configs) == 1
    c = configs[0]
    assert c.name == "remote"
    assert c.transport == "http"
    assert c.url == "https://example.com/mcp"
    assert c.headers == {"Authorization": "Bearer abc"}
    assert c.command is None


def test_parse_stdio_defaults(tmp_path):
    """args/env 缺省为空列表/空 dict。"""
    configs = _load(tmp_path, {"mcp_servers": {"demo": {"command": "python"}}}, None)
    assert configs[0].args == []
    assert configs[0].env == {}


def test_missing_command_and_url(tmp_path):
    with pytest.raises(ConfigError, match="demo"):
        _load(tmp_path, {"mcp_servers": {"demo": {"args": []}}}, None)


def test_both_command_and_url(tmp_path):
    with pytest.raises(ConfigError, match="二选一"):
        _load(
            tmp_path,
            {"mcp_servers": {"demo": {"command": "python", "url": "http://x"}}},
            None,
        )


def test_command_empty(tmp_path):
    with pytest.raises(ConfigError, match="command"):
        _load(tmp_path, {"mcp_servers": {"demo": {"command": "  "}}}, None)


def test_args_not_string_list(tmp_path):
    with pytest.raises(ConfigError, match="args"):
        _load(tmp_path, {"mcp_servers": {"demo": {"command": "p", "args": [1]}}}, None)


def test_env_not_map(tmp_path):
    with pytest.raises(ConfigError, match="env"):
        _load(tmp_path, {"mcp_servers": {"demo": {"command": "p", "env": "x"}}}, None)


def test_env_value_not_string(tmp_path):
    with pytest.raises(ConfigError, match="env"):
        _load(
            tmp_path, {"mcp_servers": {"demo": {"command": "p", "env": {"K": 1}}}}, None
        )


def test_env_undefined_var(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_MISSING", raising=False)
    with pytest.raises(ConfigError, match="MCP_MISSING"):
        _load(
            tmp_path,
            {"mcp_servers": {"demo": {"command": "p", "env": {"K": "${MCP_MISSING}"}}}},
            None,
        )


def test_headers_not_map(tmp_path):
    with pytest.raises(ConfigError, match="headers"):
        _load(tmp_path, {"mcp_servers": {"r": {"url": "http://x", "headers": []}}}, None)


def test_url_empty(tmp_path):
    with pytest.raises(ConfigError, match="url"):
        _load(tmp_path, {"mcp_servers": {"r": {"url": ""}}}, None)


def test_server_entry_not_map(tmp_path):
    with pytest.raises(ConfigError, match="demo"):
        _load(tmp_path, {"mcp_servers": {"demo": "python"}}, None)


def test_mcp_servers_not_map(tmp_path):
    with pytest.raises(ConfigError, match="必须是映射"):
        _load(tmp_path, {"mcp_servers": ["demo"]}, None)


def test_both_layers_missing(tmp_path):
    """两份文件都没有 mcp_servers 字段 → 空列表。"""
    assert _load(tmp_path, {"providers": []}, {"other": 1}) == []


def test_no_files(tmp_path):
    """两份文件都不存在 → 空列表。"""
    assert _load(tmp_path, None, None) == []


def test_merge_project_overrides_user(tmp_path, monkeypatch):
    """同名 Server 整条目覆盖，项目级盖用户级，保留用户级原位置。"""
    monkeypatch.setenv("TOKEN", "abc")
    user = {
        "mcp_servers": {
            "demo": {"command": "python", "args": ["user.py"]},
            "keep": {"command": "ls"},
        }
    }
    project = {"mcp_servers": {"demo": {"command": "python", "args": ["project.py"]}}}
    configs = _load(tmp_path, user, project)
    assert [c.name for c in configs] == ["demo", "keep"]
    assert configs[0].args == ["project.py"]  # 项目级覆盖
    assert configs[1].args == []  # 用户级保留


def test_merge_project_adds_new(tmp_path):
    """项目级新增的 Server 排在末尾。"""
    user = {"mcp_servers": {"a": {"command": "x"}}}
    project = {"mcp_servers": {"b": {"command": "y"}}}
    assert [c.name for c in _load(tmp_path, user, project)] == ["a", "b"]


def test_merge_project_missing(tmp_path):
    """项目级文件缺失 → 只返回用户级。"""
    assert [c.name for c in _load(tmp_path, {"mcp_servers": {"a": {"command": "x"}}}, None)] == ["a"]


def test_broken_yaml_raises(tmp_path):
    user_path = tmp_path / "user.yaml"
    user_path.write_text("mcp_servers: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_mcp_servers(user_path, tmp_path / "project.yaml")
