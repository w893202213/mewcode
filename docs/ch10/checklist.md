# 修复空 pattern Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] MCP 工具构造规则 pattern 为 `*`，内置工具保持精确（验证：T4 新增 2 用例通过——`exact_rule("resolve-library-id", ...)` → `"*"`；`exact_rule("run_command", {"command": "git status"})` → `"git status"`）
- [x] 空 pattern 加载规范化为 `*` 且 stderr 出现「第 N 条 pattern 为空」提示（验证：T3 用例 1 通过；真实文件加载输出「警告: 权限规则文件 .mewcode/permissions.local.yaml 第 9 条 pattern 为空，已按匹配任意（*）处理」）
- [x] 非字符串/None pattern 仍抛 ConfigError，信息清晰（验证：T3 用例 3、4 通过，报错含「pattern 无效」）

## 集成
- [x] 追加规则时旧空 pattern 被静默重写为 `*`，无重复提示（验证：T3 用例 2 通过；e2e 中追加 query-docs 规则后，文件第 9 条 `pattern: ''` 被写回为 `pattern: '*'`）
- [x] 追加幂等、保留既有规则行为不回归（验证：既有 roundtrip/preserves 用例通过）

## 编译与测试
- [x] 全量单元测试通过（验证：`.venv/bin/python -m pytest tests/ -q` → 374 passed）
- [x] 编译检查通过（验证：`.venv/bin/python -m compileall mewcode/` 无错误）

## 端到端场景（tmux，按 CLAUDE.md 要求）
- [x] 场景 1（AC1）：用当前含第 9 条空 pattern 的真实 `.mewcode/permissions.local.yaml` 启动 MewCode → 正常进入 TUI（欢迎面板、deepseek-flash）；stderr 输出「警告: …第 9 条 pattern 为空，已按匹配任意（*）处理」；无「配置错误」
- [x] 场景 2（AC2）：会话中输入「请用 context7 的 MCP 工具 query-docs 查询 Eino 框架的文档…」→ 触发 query-docs 调用 → 权限询问选 a → `.mewcode/permissions.local.yaml` 新增 `query-docs` 规则 `pattern: '*'`（旧第 9 条同时自愈为 `*`）→ 双击 Ctrl+C 退出重启，stderr 无输出、TUI 正常进入
