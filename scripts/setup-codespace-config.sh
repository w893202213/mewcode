#!/usr/bin/env bash
# 在 Codespaces / 本地首次运行时，交互式生成 ~/.mewcode/config.yaml。
# 密钥只写入当前环境，不会进入 git。
set -euo pipefail

config_dir="${HOME}/.mewcode"
config_file="${config_dir}/config.yaml"

# 去掉粘贴时可能带进来的 ANSI 转义序列 / 控制字符
clean() {
    printf '%s' "$1" \
        | LC_ALL=C sed -E 's/\x1b\[[0-9;?]*[~A-Za-z]//g' \
        | LC_ALL=C tr -d '\000-\037\177'
}

yaml_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

if [ -f "${config_file}" ]; then
    echo "配置文件已存在：${config_file}"
    read -r -p "是否覆盖？[y/N] " overwrite
    case "${overwrite}" in
        y|Y) ;;
        *) echo "已取消。"; exit 0 ;;
    esac
fi

echo "MewCode 配置向导"
echo "请选择服务商："
echo "  1) DeepSeek（OpenAI 兼容协议，默认）"
echo "  2) Anthropic（Messages 协议）"
echo "  3) 其他 OpenAI 兼容服务"
read -r -p "选择 [1/2/3]（默认 1）: " provider_choice

case "${provider_choice:-1}" in
    2)
        protocol="anthropic"
        default_model="claude-sonnet-4-5"
        default_base="https://api.anthropic.com"
        ;;
    3)
        protocol="openai"
        default_model=""
        default_base=""
        ;;
    *)
        protocol="openai"
        default_model="deepseek-chat"
        default_base="https://api.deepseek.com"
        ;;
esac

read -r -p "模型（默认 ${default_model:-必填}）: " model
model="${model:-${default_model}}"
read -r -p "base_url（默认 ${default_base:-必填}）: " base_url
base_url="${base_url:-${default_base}}"
read -r -s -p "API key: " api_key
echo

protocol="$(clean "${protocol}")"
model="$(clean "${model}")"
base_url="$(clean "${base_url}")"
api_key="$(clean "${api_key}")"

case "${protocol}" in
    openai|anthropic) ;;
    *) echo "协议必须是 openai 或 anthropic" >&2; exit 1 ;;
esac

if [ -z "${model}" ] || [[ "${model}" == *[[:space:]]* ]]; then
    echo "模型名不能为空，也不能包含空白字符" >&2
    exit 1
fi

if [[ ! "${base_url}" =~ ^https?:// ]]; then
    echo "base_url 必须以 http:// 或 https:// 开头" >&2
    exit 1
fi

if [ -z "${api_key}" ] || [[ "${api_key}" == *[[:space:]]* ]]; then
    echo "API key 不能为空，也不能包含空白字符。" >&2
    echo "如果粘贴时带入了转义序列，请重新复制纯文本 key 后再试。" >&2
    exit 1
fi

if [ "${protocol}" = "anthropic" ] && [[ "${base_url}" == *deepseek* ]]; then
    echo "配置不一致：DeepSeek 使用 OpenAI 兼容协议，请选择 1) DeepSeek。" >&2
    exit 1
fi

mkdir -p "${config_dir}"
{
    printf 'providers:\n'
    printf '  - name: default\n'
    printf '    protocol: %s\n' "${protocol}"
    printf '    model: "%s"\n' "$(yaml_escape "${model}")"
    printf '    base_url: "%s"\n' "$(yaml_escape "${base_url}")"
    printf '    api_key: "%s"\n' "$(yaml_escape "${api_key}")"
} > "${config_file}"
chmod 600 "${config_file}"

echo "已写入 ${config_file}"
echo "现在可以运行：python -m mewcode"
