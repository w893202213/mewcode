#!/usr/bin/env bash
# 在 Codespaces / 本地首次运行时，交互式生成 ~/.mewcode/config.yaml。
# 密钥只写入当前环境，不会进入 git。
#
# 用法：
#   bash scripts/setup-codespace-config.sh
#   bash scripts/setup-codespace-config.sh --provider deepseek --key-file mewcode-key.txt --non-interactive
#
# 说明：浏览器终端的剪贴板读取可能被权限拦截。此时可在编辑器里新建
# mewcode-key.txt 并粘贴 key，再用 --key-file 传入，脚本写完配置后会删除它。
set -euo pipefail

usage() {
    cat <<'EOF'
用法: bash scripts/setup-codespace-config.sh [选项]

选项:
  --provider <deepseek|anthropic|custom>  服务商，默认交互选择
  --model <name>                          模型名
  --base-url <url>                        API 地址
  --key-file <path>                       从文件读取 API key（避免终端粘贴）
  --non-interactive                       使用默认值，不询问
  --force                                 覆盖已有配置
  -h, --help                              显示帮助

环境变量:
  MEWCODE_API_KEY   未提供 --key-file 时使用该变量作为 key
EOF
}

config_dir="${HOME}/.mewcode"
config_file="${config_dir}/config.yaml"
provider_arg=""
model_arg=""
base_url_arg=""
key_file=""
non_interactive=false
force=false

while [ $# -gt 0 ]; do
    case "$1" in
        --provider)
            [ $# -ge 2 ] || { echo "--provider 缺少值" >&2; exit 2; }
            provider_arg="$2"; shift 2 ;;
        --provider=*) provider_arg="${1#*=}"; shift ;;
        --model)
            [ $# -ge 2 ] || { echo "--model 缺少值" >&2; exit 2; }
            model_arg="$2"; shift 2 ;;
        --model=*) model_arg="${1#*=}"; shift ;;
        --base-url)
            [ $# -ge 2 ] || { echo "--base-url 缺少值" >&2; exit 2; }
            base_url_arg="$2"; shift 2 ;;
        --base-url=*) base_url_arg="${1#*=}"; shift ;;
        --key-file)
            [ $# -ge 2 ] || { echo "--key-file 缺少值" >&2; exit 2; }
            key_file="$2"; shift 2 ;;
        --key-file=*) key_file="${1#*=}"; shift ;;
        --non-interactive) non_interactive=true; shift ;;
        --force) force=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "未知参数: $1" >&2; usage; exit 2 ;;
    esac
done

# 去掉粘贴时可能带进来的 ANSI 转义序列 / 控制字符
clean() {
    printf '%s' "$1" \
        | LC_ALL=C sed -E 's/\x1b\[[0-9;?]*[~A-Za-z]//g' \
        | LC_ALL=C tr -d '\000-\037\177'
}

yaml_escape() {
    printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

if [ -f "${config_file}" ] && [ "${force}" = false ] && [ "${non_interactive}" = false ]; then
    echo "配置文件已存在：${config_file}"
    read -r -p "是否覆盖？[y/N] " overwrite
    case "${overwrite}" in
        y|Y) ;;
        *) echo "已取消。"; exit 0 ;;
    esac
fi

if [ -f "${config_file}" ] && { [ "${force}" = true ] || [ "${non_interactive}" = true ]; }; then
    echo "将覆盖已有配置：${config_file}"
fi

if [ -n "${provider_arg}" ]; then
    case "${provider_arg}" in
        deepseek|1) choice=1 ;;
        anthropic|2) choice=2 ;;
        custom|3) choice=3 ;;
        *) echo "--provider 必须是 deepseek / anthropic / custom" >&2; exit 2 ;;
    esac
elif [ "${non_interactive}" = true ]; then
    choice=1
else
    echo "MewCode 配置向导"
    echo "请选择服务商："
    echo "  1) DeepSeek（OpenAI 兼容协议，默认）"
    echo "  2) Anthropic（Messages 协议）"
    echo "  3) 其他 OpenAI 兼容服务"
    read -r -p "选择 [1/2/3]（默认 1）: " provider_choice
    case "${provider_choice:-1}" in
        2) choice=2 ;;
        3) choice=3 ;;
        *) choice=1 ;;
    esac
fi

case "${choice}" in
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

model="${model_arg:-${default_model}}"
base_url="${base_url_arg:-${default_base}}"

if [ "${non_interactive}" = false ]; then
    if [ -z "${model_arg}" ]; then
        read -r -p "模型（默认 ${default_model:-必填}）: " model_input
        model="${model_input:-${default_model}}"
    fi
    if [ -z "${base_url_arg}" ]; then
        read -r -p "base_url（默认 ${default_base:-必填}）: " base_input
        base_url="${base_input:-${default_base}}"
    fi
fi

if [ -n "${key_file}" ]; then
    if [ ! -r "${key_file}" ]; then
        echo "找不到或无法读取 key 文件：${key_file}" >&2
        exit 1
    fi
    api_key="$(cat "${key_file}")"
elif [ -n "${MEWCODE_API_KEY:-}" ]; then
    api_key="${MEWCODE_API_KEY}"
elif [ "${non_interactive}" = true ]; then
    echo "非交互模式需要 --key-file 或环境变量 MEWCODE_API_KEY" >&2
    exit 1
else
    read -r -s -p "API key: " api_key
    echo
fi

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

if [ -n "${key_file}" ]; then
    rm -f "${key_file}"
    echo "已删除临时 key 文件：${key_file}"
fi

echo "已写入 ${config_file}"
echo "现在可以运行：python -m mewcode"
