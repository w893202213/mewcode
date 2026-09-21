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


# ---------------------------------------------------------------------------
# ch06: custom_instructions 读取
# ---------------------------------------------------------------------------

from mewcode.config import load_custom_instructions


def test_custom_instructions_present(tmp_path):
    """顶层 custom_instructions 多行文本原样读取（前后空白剔除）。"""
    path = tmp_path / "config.yaml"
    path.write_text(
        "providers: []\ncustom_instructions: |\n  回答用中文\n  代码风格简洁\n",
        encoding="utf-8",
    )
    # 注意：providers 空列表与 load_config 无关，本函数只读顶层字段
    text = load_custom_instructions(str(path))
    assert text == "回答用中文\n代码风格简洁"


def test_custom_instructions_absent_returns_empty(tmp_path):
    """字段缺失返回空串（该模块不参与拼装）。"""
    path = _write(tmp_path, [FULL])
    assert load_custom_instructions(path) == ""


def test_custom_instructions_non_string_returns_empty(tmp_path):
    """字段非字符串（如数字/列表）返回空串，不抛异常。"""
    path = tmp_path / "config.yaml"
    path.write_text("providers: []\ncustom_instructions: 123\n", encoding="utf-8")
    assert load_custom_instructions(str(path)) == ""


def test_custom_instructions_missing_file_returns_empty(tmp_path):
    """文件缺失返回空串（存在性校验由 load_config 负责）。"""
    assert load_custom_instructions(str(tmp_path / "nope.yaml")) == ""


def test_custom_instructions_bad_yaml_raises(tmp_path):
    """文件损坏抛 ConfigError（与 load_config 口径一致）。"""
    path = tmp_path / "bad.yaml"
    path.write_text("providers: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="格式错误"):
        load_custom_instructions(str(path))
