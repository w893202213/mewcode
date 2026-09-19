"""配置层测试。"""

import pytest
import yaml

from mewcode.config import ConfigError, ProviderConfig, load_config, select_profile


def _write(tmp_path, providers: list[dict]) -> str:
    """把 providers 列表写成临时 YAML 文件，返回路径。"""
    path = tmp_path / "config.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"providers": providers}, f)
    return str(path)


# 一份完整字段的配置模板
FULL = {
    "name": "claude",
    "protocol": "anthropic",
    "model": "claude-sonnet-4-5",
    "base_url": "https://api.anthropic.com",
    "api_key": "sk-ant-test",
}


def test_load_two_configs(tmp_path):
    path = _write(tmp_path, [FULL, {**FULL, "name": "gpt", "protocol": "openai"}])
    configs = load_config(path)
    assert len(configs) == 2
    assert configs[0].name == "claude"
    assert configs[0].protocol == "anthropic"
    assert configs[1].name == "gpt"
    assert configs[1].protocol == "openai"


def test_thinking_defaults_false(tmp_path):
    path = _write(tmp_path, [FULL])  # 无 thinking 字段
    assert load_config(path)[0].thinking is False


def test_thinking_true(tmp_path):
    path = _write(tmp_path, [{**FULL, "thinking": True}])
    assert load_config(path)[0].thinking is True


@pytest.mark.parametrize("missing", ["name", "protocol", "model", "base_url", "api_key"])
def test_missing_required_field(tmp_path, missing):
    item = {k: v for k, v in FULL.items() if k != missing}
    path = _write(tmp_path, [item])
    with pytest.raises(ConfigError, match=missing):
        load_config(path)


def test_invalid_protocol(tmp_path):
    path = _write(tmp_path, [{**FULL, "protocol": "gemini"}])
    with pytest.raises(ConfigError, match="protocol"):
        load_config(path)


def test_thinking_must_be_bool(tmp_path):
    path = _write(tmp_path, [{**FULL, "thinking": "yes"}])
    with pytest.raises(ConfigError, match="thinking"):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(str(tmp_path / "nope.yaml"))


def test_bad_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("providers: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(str(path))


def test_select_profile_hit_and_default(tmp_path):
    path = _write(tmp_path, [FULL, {**FULL, "name": "gpt", "protocol": "openai"}])
    configs = load_config(path)
    assert select_profile(configs) is configs[0]  # 缺省取第一份
    assert select_profile(configs, "gpt") is configs[1]


def test_select_profile_not_found_lists_available(tmp_path):
    path = _write(tmp_path, [FULL, {**FULL, "name": "gpt", "protocol": "openai"}])
    configs = load_config(path)
    with pytest.raises(ConfigError) as exc:
        select_profile(configs, "nope")
    assert "claude" in str(exc.value) and "gpt" in str(exc.value)
