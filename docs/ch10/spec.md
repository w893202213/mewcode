# 修复 MCP 工具永久允许写入空 pattern 导致启动失败 Spec

## 背景

权限规则形式为「工具名(模式)」：六个内置工具（run_command + 五个文件工具）用主参数做 pattern 精确匹配；MCP 工具没有参数级 pattern 概念，`_rule_value`（`mewcode/permissions/gate.py`）对它们一律返回空字符串。

对 MCP 工具调用选择「a=永久允许」时，`exact_rule` 以空串为 pattern，经 `append_to_local_file` 写入本地级 YAML。下次启动时 `_parse_rule_file` 校验 `not pattern` 拒绝，抛 ConfigError「第 9 条 pattern 缺失」，MewCode 无法启动——已经实际发生（`.mewcode/permissions.local.yaml` 第 9 条 `resolve-library-id`）。

补充说明：空 pattern 规则在运行期语义本就是「该工具全部放行」——MCP 工具的匹配 value 恒为空串，空 pattern 精确相等恒命中。因此修复为 `*` 不改变既有规则的权限面。

## 目标

1. MewCode 不再因空 pattern 规则拒启
2. 对 MCP 工具选「永久允许」写入合法规则，杜绝复发
3. 已存在的坏文件无需手工修改即可启动，且授权语义保持不变

## 功能需求

- F1: MCP 工具规则使用 `*` 作为 pattern。对无参数级 pattern 概念的工具（即 `_rule_value` 当前返回空串的所有情况），构造规则时 pattern 取 `*`（glob 匹配任意值），「本会话允许」和「永久允许」两条路径都生效。
- F2: 加载时空 pattern 规范化为 `*` 并提示。读取规则文件时，pattern 为空字符串的条目不报错，按 `*` 载入，并向用户打印一条中文提示（含文件路径与条目序号，如「权限规则文件 X 第 9 条 pattern 为空，已按匹配任意处理」）。
- F3: 结构性错误仍然报错。pattern 缺失（None）或非字符串时，维持现状抛 ConfigError，不做规范化。

## 非功能需求

- N1: 提示可定位。规范化提示为中文，包含文件路径与条目序号，用户可据此找到并手动修正 YAML。
- N2: 内置工具语义不变。六个内置工具的 pattern 生成与匹配逻辑不受影响；`run_command(git status)` 永久允许后写入的仍是精确命令串。
- N3: 不引入新依赖。修复仅涉及现有 permissions 模块，不加第三方库。

## 不做的事

- 不做 MCP 调用参数级 pattern。不引入按 MCP 工具 arguments JSON 内容匹配的模式语法，超出本次修复范围。
- 不做坏文件的显式写回迁移。内存中规范化为 `*` 即可；文件内容保持原样，仅在下次追加规则时自然重写（此时 `append_to_local_file` 读到的已是规范化后的规则）。
- 不改权限询问交互与文案。TUI 的「y/s/a/n」流程不变。
- 不做启动自愈之外的其他启动异常处理。其他 ConfigError 行为保持原样。

## 验收标准

- AC1（对应 F2）: 用当前含第 9 条空 pattern 的 `.mewcode/permissions.local.yaml` 启动 MewCode，正常进入 TUI，且启动输出包含「第 9 条 pattern 为空，已按匹配任意处理」类提示。
- AC2（对应 F1）: 会话中对 MCP 工具选「a=永久允许」，检查本地 YAML 新增规则 `pattern: '*'`；重启 MewCode 无报错。
- AC3（对应 N2）: 对 `run_command` 选「a=永久允许」后，写入的 pattern 仍为该次精确命令串（无 `*` 化）。
- AC4（对应 F3）: 手写一条 `pattern:` 缺失（None）的规则后启动，仍抛 ConfigError 且信息清晰。
- AC5（回归）: 现有单元测试（`test_gate.py`、`test_rules.py` 等）全部通过。
