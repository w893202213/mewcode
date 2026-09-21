# 五层防御权限系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。对应 spec 验收标准见括号。

## 实现完整性

- [x] 黑名单 9 条正反例全部命中/不误伤（验证：`pytest tests/test_blacklist.py -q` 全绿）（AC1）
- [x] 沙箱越界/符号链接逃逸/项目内放行判定正确（验证：`pytest tests/test_sandbox.py -q` 全绿）（AC2）
- [x] 规则匹配与合并裁决：精确/glob、deny>ask>allow、无命中回默认（验证：`pytest tests/test_rules.py -q` 全绿）（AC3、AC6）
- [x] 三 YAML 合并读取、损坏/非法条目报错、追加写回一致（验证：`pytest tests/test_rules.py -q` 全绿）（AC3）
- [x] 闸门层序短路与三档兜底、accept 下 deny 仍拦（验证：`pytest tests/test_gate.py -q` 全绿）（AC4）
- [x] permission_mode 配置解析三态与非法值报错（验证：`pytest tests/test_config.py -q` 全绿）（AC4）
- [x] Agent Loop：ask 事件/应答/取消解除 pending；五种拒绝路径均以 RoundEnd(OK) 收尾（验证：`pytest tests/test_permission_loop.py -q` 全绿）（AC5、AC7）

## 集成

- [x] CLI 装配三规则文件+模式并传入 TUI，欢迎语显示「权限模式: default」（验证：`python -m mewcode --help` 正常；tmux 启动观察欢迎语）（AC3、AC4）
- [x] `/mode` 三档循环切换且反馈档位中文名（验证：tmux 连按 /mode 观察反馈变化）（AC4）
- [x] TUI 询问面板四选项 y/s/a/n 各自生效（验证：tmux 逐项操作观察）（AC5）
- [x] 永久允许写入本地级 YAML 且重启后不再询问（验证：tmux 按 a → `cat .mewcode/permissions.local.yaml` 见规则 → 重启复验）（AC5）
- [x] 既有功能回归：Agent Loop 与 Plan Mode 全量单测（验证：`pytest tests/test_agent_loop.py tests/test_plan_mode.py -q` 全绿）（AC7）
- [x] ch05 checklist 场景 5 与集成条目已同步 N4 行为变更（验证：通读确认）

## 编译与测试

- [x] 全量单元测试通过（验证：`python -m pytest -q` 全绿）
- [x] 语法检查通过（验证：`python -m compileall mewcode/ scripts/` 无输出错误）

## 端到端场景（tmux + mock LLM）

- [x] 场景 1（黑名单+沙箱+询问同轮）：danger-model 提问 → 观察 `run_command("sudo rm -rf /")` 拒绝面板（黑名单原因）、`write_file("../escape.txt")` 拒绝面板（路径越界原因）、`write_file("note_ok.txt")` 弹出询问面板 → 按 n 拒绝 → 循环正常收尾、无命令实际执行（AC1、AC2、AC5、AC7）
- [x] 场景 2（询问四选项）：loop-model 提问 → 第 2 轮 `run_command("echo hello from loop")` 弹出询问 → 验证 y（本次放行，再次提问仍问）、s（同会话再次提问不再问）、n（拒绝面板且循环继续）（AC5）
- [x] 场景 3（永久允许）：loop-model 询问中按 a → 本地级 YAML 出现 `run_command("echo hello from loop")` allow 规则 → 重启 mewcode 再提问 → 不再询问直接执行（AC5）
- [x] 场景 4（/mode 三档）：default 下询问 → `/mode` 切 strict → 同请求的 run_command 直接拒绝面板 → `/mode` 切 accept → 直接执行不询问 → `/mode` 切回 default 恢复询问（AC4）
- [x] 场景 5（规则文件）：项目级 permissions.yaml 手写 deny 规则 `run_command(echo *)` → loop-model 的 run_command 被拒（deny 压过 default 询问）；再把该文件改成损坏 YAML → 重启 mewcode 启动报配置错误退出（AC3、AC6）
- [x] 场景 6（询问中取消）：loop-model 询问面板弹出时按 Ctrl+C → 「已取消」面板、回到输入行，不悬挂（AC7）
- [x] 场景 7（既有行为回归）：Plan Mode 照旧（/plan 只读调研 → /do 恢复全工具）；读类工具不询问直接执行（AC7）
