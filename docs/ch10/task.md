# 修复空 pattern 任务拆解 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/permissions/gate.py` | `_rule_value` 回退分支 `""` → `"*"`（F1） |
| 修改 | `mewcode/permissions/rules.py` | 校验拆分 + 空串规范化 `*` + warn 参数（F2/F3） |
| 修改 | `tests/test_rules.py` | 空 pattern 规范化/提示/静默、None/非字符串报错用例 |
| 修改 | `tests/test_gate.py` | MCP 工具 exact_rule 为 `*`、run_command 保持精确用例 |

## T1: gate.py 回退分支改为 `*`

**文件：** `mewcode/permissions/gate.py`
**依赖：** 无
**步骤：**
1. 定位 `_rule_value` 末尾回退 `return ""`
2. 改为 `return "*"`，注释「# MCP 等无参数级 pattern 概念的工具：匹配任意」

**验证：** `.venv/bin/python -m pytest tests/test_gate.py tests/test_rules.py -q` 现有用例全绿

## T2: rules.py 校验拆分 + 空串规范化

**文件：** `mewcode/permissions/rules.py`
**依赖：** T1
**步骤：**
1. `_parse_rule_file` 签名增加 `*, warn: bool = True` 参数
2. 原 `if not isinstance(pattern, str) or not pattern:` 拆为两条：
   - 非字符串（含 None）→ 抛 `ConfigError(f"...第 {i} 条 pattern 无效: {pattern!r}")`
   - 空字符串 → `warn` 为真时打印 stderr：`警告: 权限规则文件 {path} 第 {i} 条 pattern 为空，已按匹配任意（*）处理`，然后 `pattern = "*"`
3. 文件顶部补充 `import sys`（若尚无）
4. `append_to_local_file` 读取旧文件处改传 `warn=False`

**验证：** `.venv/bin/python -m pytest tests/test_rules.py -q` 现有用例全绿

## T3: test_rules.py 新增 4 个用例

**文件：** `tests/test_rules.py`
**依赖：** T2
**步骤：** 按文件既有风格（tmp_path + `_write_rules` 辅助）新增：
1. `test_load_from_empty_pattern_normalized_with_warning(tmp_path, capsys)` — 空 pattern 文件 → load_from → 规则 pattern 为 `"*"`、verdict 命中；capsys 断言 stderr 含「第 1 条 pattern 为空」
2. `test_append_normalizes_silently(tmp_path, capsys)` — 含空 pattern 的旧文件 append 一条 → 写回文件中 pattern 为 `*`，且 stderr 无提示
3. `test_load_from_none_pattern_raises(tmp_path)` — `pattern:` 缺失 → ConfigError
4. `test_load_from_non_string_pattern_raises(tmp_path)` — `pattern: 123` → ConfigError，信息含「pattern 无效」

**验证：** `.venv/bin/python -m pytest tests/test_rules.py -q` 全绿

## T4: test_gate.py 新增 2 个用例

**文件：** `tests/test_gate.py`
**依赖：** T1
**步骤：**
1. 若未导入 `exact_rule`，在 import 行补充
2. `test_exact_rule_mcp_tool_star()` — `exact_rule("resolve-library-id", {"libraryName": "Eino"}, Action.ALLOW)` → `pattern == "*"`
3. `test_exact_rule_run_command_exact()` — `exact_rule("run_command", {"command": "git status"}, Action.ALLOW)` → `pattern == "git status"`

**验证：** `.venv/bin/python -m pytest tests/test_gate.py -q` 全绿

## T5: 全量回归 + 真实坏文件验证

**依赖：** T1–T4
**步骤：**
1. 全量：`.venv/bin/python -m pytest tests/ -q` 全绿
2. 用当前真实坏文件 `.mewcode/permissions.local.yaml` 跑加载脚本，断言不再抛 ConfigError、stderr 出现「第 9 条 pattern 为空」提示

**验证：** 全量测试绿；加载脚本输出含预期提示且退出码 0

## 执行顺序

```
T1 → T2 → T3 ↘
              T5
        T4 ↗
```
