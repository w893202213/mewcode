# ch13 技能系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 技能文件解析：合法 frontmatter 全字段解析（含缺省值）；缺 name/description、字段类型不对、无 frontmatter、YAML 语法错误 → 跳过并警告（验证：`pytest tests/test_skills_model.py -q` 18 例全过）
- [x] 参数占位符：`$ARGS`/`$ARGUMENTS` 替换为用户传入内容，未传参数替换为空串（验证：单测 + tmux 场景 2 查 log：独立对话系统提示含「补充说明（如有）：fix the login」）
- [x] 三级扫描：项目 > 用户 > 内置同名覆盖；同层重名警告且后者跳过；坏文件跳过不阻断；目录型技能（SKILL.md + dir 字段）正确发现（验证：`pytest tests/test_skills_discover.py -q` 10 例全过）
- [x] 管理器：激活/重复激活幂等/热更新重读/白名单并集/pending 单槽/clear 全清（验证：`pytest tests/test_skills_manager.py -q` 13 例全过）
- [x] use_skill 工具：shared 返回完整 SOP；independent 登记 pending 并返回「本轮结束后执行」；未知技能报错（验证：`pytest tests/test_use_skill_tool.py -q` 5 例全过 + tmux 场景 1/3）
- [x] 系统提示：【技能】块在 stable（缓存前缀）；【已激活技能】块在 dynamic、紧跟补充块之后、记忆之前，每轮 build 现取（验证：`pytest tests/test_skills_builder.py -q` 6 例全过 + tmux 查 mock_requests.log 请求体 system 字段）
- [x] Agent 白名单：激活后可见工具 = 白名单 ∪ use_skill；白名单外工具调用回灌失败结果（验证：`pytest tests/test_skills_agent.py -q` 5 例全过 + tmux 查 log 工具列表收窄为 `[run_command, use_skill]`）
- [x] 命令层：技能命令注册/与内置命令撞名跳过/`unregister` 撤销（验证：`pytest tests/test_skills_commands.py -q` 6 例全过）
- [x] 内置三个样板 commit/review/test 可解析且字段符合预期，test 为目录型（验证：`pytest tests/test_skills_discover.py -q` 内置断言全过）

## 集成

- [x] cli 装配链：启动后系统提示含【技能】块列出全部生效技能；use_skill 注册进 registry（验证：tmux 查 mock_requests.log 请求体 system 含【技能】清单与三个样板、tools 含 use_skill；全量单测）
- [x] 白名单启动校验：项目技能引用不存在的工具 → 启动 stderr 指明技能与工具并 exit 1（验证：tmux 场景 4：`配置错误: 技能 badskill 白名单引用未知工具: no_such_tool` + 退出码 1；移除后恢复正常启动）
- [x] /review 迁移：内置命令 12→11 条，review 技能承接原提示词（验证：`pytest tests/test_command_builtins.py tests/test_command_registry.py -q` 全过 + tmux：未加载时 /review 提示未知命令、加载后 /review 注入 SOP 审查）
- [x] 独立执行不落会话存档：独立对话消息不进 .mewcode/sessions JSONL，只有摘要（主对话）落盘（验证：tmux 场景 1 后检查存档：含「独立执行摘要」轮、不含「请执行技能 commit」与内层助手话术）
- [x] 命令实现与渲染框架解耦：`mewcode/skills/` 包内无 Rich / prompt_toolkit import（验证：`grep -rn "rich\|prompt_toolkit" mewcode/skills/` 无结果）

## 编译与测试

- [x] 项目编译无错误（`python -m compileall mewcode/`）
- [x] 全量单测通过（`pytest tests/ -q`，583 例全过：既有 516 例含 /review 迁移更新 + 新增 67 例）

## 端到端场景（tmux 实测）

- [x] 场景 1 模型路径独立执行：启动（mock skill-commit-model）→ 输入「commit my changes please」→ 模型调 use_skill(commit) → 显示「本轮结束后执行」→ 独立执行流式可见（git status 工具调用与结果面板、权限弹窗正常）→ 摘要面板 → 自动续一轮汇报；mock_requests.log 验证工具列表收窄为 run_command + use_skill
- [x] 场景 2 斜杠命令与生命周期：commit 激活后输入 `/commit fix the login` 直接触发独立执行（log 中 SOP 的 $ARGS 已替换为「fix the login」）；`/help` 含 /commit（带描述）；`/clear` 后 `/commit` 提示未知命令
- [x] 场景 3 shared 技能：模型激活 review（工具结果返回完整 SOP）后 `/review 重点看安全` 注入审查 SOP，模型在主对话审查（审查结论留在主历史：后续请求历史含「审查完成：改动整体良好」与注入的 SOP；工具列表 = review 白名单 ∪ use_skill）
- [x] 场景 4 启动失败：项目 .mewcode/skills/ 放一个 tools 引用不存在工具的技能 → 启动报错退出（exit 1），stderr 指出技能名与工具名；移除后恢复正常启动

## 兼容回归

- [x] ch12 其余 11 条命令行为不回退（/help 列出 11 条内置命令 + review 技能未加载时 /review 提示未知命令；/status、/compact、/plan、/do 冒烟通过）
- [x] ch09/ch11 验收场景不回退（/compact 实测「上下文较短，无需压缩」；/resume、记忆注入由全量单测覆盖）
