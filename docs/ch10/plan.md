# 修复 MCP 工具永久允许写入空 pattern 导致启动失败 Plan

## 架构概览

修复分两端，均落在 permissions 模块内：

- **写入端（gate.py）**：`_rule_value` 对无参数级 pattern 概念的工具（回退分支）返回 `"*"` 而非 `""`。此后「本会话允许/永久允许」构造的规则 pattern 恒为合法值，运行期匹配值也是 `"*"`，`*`==`*` 精确相等命中，写入与匹配两端一致。
- **加载端（rules.py）**：`_parse_rule_file` 校验拆分——pattern 非字符串（含 None）仍抛 ConfigError（F3）；空字符串规范化为 `"*"` 并向 stderr 打印提示（F2）。`append_to_local_file` 内部读取旧文件时传 `warn=False` 静默规范化，避免每次追加都重复提示；下次追加写回时旧空 pattern 自然重写为 `*`，文件自愈。

## 核心数据结构

`Rule` 数据类不变（tool/pattern/action 三字段，pattern 恒为非空字符串）。

`_parse_rule_file` 签名增加一个关键字参数：

```python
@staticmethod
def _parse_rule_file(path: Path, *, warn: bool = True) -> list[Rule]:
```

- `warn=True`（启动加载）：空 pattern 规范化时打印提示
- `warn=False`（追加写回前读取）：只规范化，不提示

## 模块设计

### mewcode/permissions/gate.py（写入端，F1）

`_rule_value` 回退分支改动（其余分支不动）：

```python
def _rule_value(tool_name: str, args: dict) -> str:
    if tool_name == "run_command":
        return str(args.get("command", ""))
    if tool_name in {"read_file", "write_file", "edit_file"}:
        return str(args.get("file_path", ""))
    if tool_name in {"find_files", "grep_code"}:
        return str(args.get("pattern", ""))
    return "*"   # MCP 等无参数级 pattern 概念的工具：匹配任意
```

`exact_rule`、`grant_session`、`grant_permanent` 均不改——它们已把 `_rule_value` 的返回值直接用作 pattern。

### mewcode/permissions/rules.py（加载端，F2/F3）

`_parse_rule_file` 校验拆分，空串规范化并提示：

```python
if not isinstance(pattern, str):
    raise ConfigError(f"权限规则文件 {path} 第 {i} 条 pattern 无效: {pattern!r}")
if not pattern:
    if warn:
        print(f"警告: 权限规则文件 {path} 第 {i} 条 pattern 为空，已按匹配任意（*）处理", file=sys.stderr)
    pattern = "*"
```

- `_load_file` 走默认 `warn=True`（启动提示）
- `append_to_local_file` 读取旧文件时传 `warn=False`（静默规范化）

## 模块交互

1. **启动**：`cli._build_gate` → `RuleSet.load_from` → `_load_file` → `_parse_rule_file(warn=True)` → 空 pattern 规范化 `*` + stderr 提示 → 内存规则生效
2. **永久允许**：TUI `_ask_user` 选 a → `gate.grant_permanent(exact_rule(...))` → `append_to_local_file` → `_parse_rule_file(warn=False)` 读旧文件（静默规范化）→ 追加新规则（pattern `*`）→ `safe_dump` 写回（旧空 pattern 一并重写为 `*`，文件自愈）
3. **运行期匹配**：`gate.check` → `_rule_value(MCP 工具)` 返回 `*` → `match(tool, "*")` 与规则 pattern `*` 精确相等命中

## 文件组织

```
mewcode/
├── permissions/
│   ├── gate.py   — 修改：_rule_value 回退分支 "" → "*"（F1）
│   └── rules.py  — 修改：_parse_rule_file 校验拆分 + warn 参数（F2/F3）；
│                   append_to_local_file 传 warn=False
tests/
├── test_gate.py   — 修改：MCP 工具 exact_rule 的 pattern 为 "*"；run_command 保持精确命令
└── test_rules.py  — 修改：空 pattern 规范化 + 提示输出断言；非字符串/None 报错用例
docs/ch10/         — 四份文档
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 修复点 | 改 `_rule_value` 回退分支，而非在 `exact_rule` 兜底 | 单一事实来源：运行期匹配值与写入 pattern 同源，`*`==`*` 精确相等命中；旧文件规范化后的 `*` 规则同样命中 |
| 提示通道 | `print(..., file=sys.stderr)`，前缀「警告: 」 | 与 `mcp/manager.py:27` 既有惯例一致；发生在 TUI 启动前，不干扰渲染 |
| 追加时静默 | `_parse_rule_file(warn=False)` | 提示只在启动出现一次；追加写回时旧空 pattern 自然重写为 `*`，文件自愈 |
| 非字符串报错文案 | 「pattern 无效: {值!r}」 | 与「为空已规范化」区分，手写错误可定位（AC4） |
| MCP 工具 deny/glob 兼容 | 不额外处理 | 修复前后 MCP 工具唯一有效的 pattern 都是 `*`（修复前值 ""、修复后值 `*` 均只被 `*` 匹配），无回归 |
