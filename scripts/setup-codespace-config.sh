#!/usr/bin/env bash
# 在 Codespaces / 本地首次运行时，交互式生成 ~/.mewcode/config.yaml。
# 密钥只写入当前环境，不会进入 git。
set -euo pipefail

config_dir="${HOME}/.mewcode"
config_file="${config_dir}/config.yaml"

if [ -f "${config_file}" ]; then
    echo "配置文件已存在：${config_file}"
    echo "如需重新生成，请先删除它。"
    exit 0
fi

echo "MewCode 配置向导"
read -r -p "协议 [openai/anthropic]（默认 openai）: " protocol
protocol="${protocol:-openai}"

if [ "${protocol}" = "anthropic" ]; then
    default_model="claude-sonnet-4-5"
    default_base="https://api.anthropic.com"
else
    protocol="openai"
    default_model="deepseek-chat"
    default_base="https://api.deepseek.com"
fi

read -r -p "模型（默认 ${default_model}）: " model
model="${model:-${default_model}}"
read -r -p "base_url（默认 ${default_base}）: " base_url
base_url="${base_url:-${default_base}}"
read -r -s -p "API key: " api_key
echo

if [ -z "${api_key}" ]; then
    echo "API key 不能为空" >&2
    exit 1
fi

mkdir -p "${config_dir}"
cat > "${config_file}" <<EOF
providers:
  - name: default
    protocol: ${protocol}
    model: ${model}
    base_url: ${base_url}
    api_key: ${api_key}
EOF
chmod 600 "${config_file}"

echo "已写入 ${config_file}"
echo "现在可以运行：mewcode"
