# ch12 斜杠命令系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 命令注册中心可用：register / validate / resolve（含别名）/ visible 行为正确（验证：`pytest tests/test_command_registry.py -q` 全过）
- [ ] 别名冲突启动即失败：撞名注册 → `build_registry` 抛 `CommandConflictError`，cli 层 stderr 输出冲突详情并 exit(1)（验证：单测断言异常与详情文本；cli 分支目测）
- [ ] 斜杠解析器三态正确：空白与普通文本不进入命令路径；`/HELP` 与 `/help` 等价；`/help␣␣abc` 参数为 ` abc`（验证：`pytest tests/test_command_parser.py -q` 全过）
- [ ] 12 条内置命令全部登记且行为符合 F7（验证：`pytest tests/test_command_builtins.py -q` 全过）
- [ ] 补全器：单匹配直接补主名、多匹配弹菜单、别名命中补主名、隐藏命令不参与（验证：`pytest tests/test_command_completion.py -q` 全过）
- [ ] `/help` 动态生成：列出全部可见命令（含别名展示），`/help <命令>` 显示单条详情（验证：单测 + 真实 TUI 目测）

## 集成

- [ ] 回车分流器：斜杠命令走 dispatch，普通文本送 Agent，空白静默继续（验证：`pytest tests/test_command_dispatch.py -q` 全过 + tmux 场景 1）
- [ ] `/resume` 纳入统一分发路径：主循环无特殊拦截，多步交互经 UiController 完成（验证：`grep -n "resume" mewcode/tui/app.py` 主循环无特殊分支 + tmux 场景 4）
- [ ] 状态栏联动：`/plan`、`/do`、`/permission` 切换后状态栏立即刷新（验证：tmux 场景 1 观察标记变化）
- [ ] 轮末 record_usage：对话一轮后 `/status` 的最近一轮与累计 token 更新（验证：tmux 场景 1：先对话一轮再 /status，数值非零）
- [ ] 命令实现与渲染框架解耦：`mewcode/commands/` 包内无 Rich / prompt_toolkit import（验证：`grep -rn "rich\|prompt_toolkit" mewcode/commands/` 无结果）

## 编译与测试

- [ ] 项目编译无错误（`python -m compileall mewcode/`）
- [ ] 全量单测通过（`pytest tests/ -q`，既有 441 例 + 新增用例全过）

## 端到端场景（tmux 实测）

- [ ] 场景 1 命令总线：启动 → `/help` 列出 12 条命令 → `/status` 立即显示状态且期间无模型请求 → `/plan` 状态栏变 `[DEFAULT][PLAN]` → `/do` 恢复 `[DEFAULT]` → `/permission` 档位切换、状态栏同步 → `/exit` 退出
- [ ] 场景 2 补全：输入 `/cl` 按 Tab 直接补全 `/clear`；输入 `/c` 按 Tab 弹菜单（含 `/clear`、`/compact`）；输入 `/mo` 按 Tab 补出 `/permission`
- [ ] 场景 3 提示词类：输入 `/review` → Agent 收到 git diff 审查预设提示词并开始一轮回复（本地无其他副作用）
- [ ] 场景 4 多步交互：输入 `/resume` → 列出最近会话面板 → 输入编号恢复 → 恢复反馈与欢迎信息正常
- [ ] 场景 5 别名与兼容：`/q` 等价退出；`/mode` 循环切换权限档（与 `/permission` 等价）；`/HELP` 与 `/help` 输出一致；`/nope` 提示未知命令并引导 `/help`
