# 修复空 pattern Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [ ] MCP 工具构造规则 pattern 为 `*`，内置工具保持精确（验证：T4 新增 2 用例通过）
- [ ] 空 pattern 加载规范化为 `*` 且 stderr 出现「第 N 条 pattern 为空」提示（验证：T3 用例 1 通过）
- [ ] 非字符串/None pattern 仍抛 ConfigError，信息清晰（验证：T3 用例 3、4 通过）

## 集成
- [ ] 追加规则时旧空 pattern 被静默重写为 `*`，无重复提示（验证：T3 用例 2 通过）
- [ ] 追加幂等、保留既有规则行为不回归（验证：既有 roundtrip/preserves 用例通过）

## 编译与测试
- [ ] 全量单元测试通过（验证：`.venv/bin/python -m pytest tests/ -q` 全绿）
- [ ] 编译检查通过（验证：`.venv/bin/python -m compileall mewcode/` 无错误）

## 端到端场景（tmux，按 CLAUDE.md 要求）
- [ ] 场景 1（AC1）：用当前含第 9 条空 pattern 的真实 `.mewcode/permissions.local.yaml` 启动 MewCode → 正常进入 TUI；启动输出含「第 9 条 pattern 为空，已按匹配任意（*）处理」；无「配置错误」
- [ ] 场景 2（AC2）：会话中输入会触发 MCP 工具调用的请求（如「用 resolve-library-id 查一下 Eino」）→ 权限询问选 a → 检查 `.mewcode/permissions.local.yaml` 新增规则 `pattern: '*'` → 重启 MewCode 无报错
