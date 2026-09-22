# ch11 记忆系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 三层指令加载与 @include 展开已实现（验证：`python -m pytest tests/test_memory_instructions.py -q` → 12 passed）
- [x] 会话存档（ID 生成/追加写/扫描现算/过期清理）已实现（验证：`tests/test_memory_sessions.py` → 16 passed，覆盖 ID 格式/撞车重试/往返/倒序扫描/清理）
- [x] 恢复加载（坏行跳过/截断/时间跨度提醒）已实现（验证：同文件坏行计数/未配平截断/半字节尾巴/提醒阈值用例通过）
- [x] 自动笔记（NoteManager/NoteUpdater/提示模板与解析）已实现（验证：`tests/test_memory_notes.py` → 19 passed，含 busy 跳过/失败静默/解析容错）
- [x] 记忆索引注入与恢复压缩入口已实现（验证：`tests/test_memory_integration.py` + `tests/test_prompt.py` → 全部通过）
- [x] `--resume` 参数与 `/resume` 命令可用（验证：`python -m mewcode --help` 输出含 `--resume [SESSION_ID]`；e2e 中 `/resume` 列表正常展示）

## 集成

- [x] 启动装配链路：cli 加载三层指令 → build_memory_context → TUI 接收 MemoryContext（验证：`test_memory_integration.py` 装配测试通过；e2e 启动输出含「新会话 <id>」与欢迎面板信息行）
- [x] 每轮结束后新消息落盘 JSONL、OK 收尾触发笔记异步更新（验证：e2e 场景 1——JSONL 7 行全部合法、类型序列正确；轮后出现「📝 记忆已更新：1 条新增」提示）
- [x] 记忆索引每轮请求前注入 system prompt dynamic 部分（验证：单元测试断言 dynamic 含索引内容且位于指令块前；e2e 场景 1 第二轮模型主动说「该偏好已在你的用户级记忆中，后续会话也会生效」）
- [x] 恢复的会话可继续走压缩/权限/Plan Mode 全套既有机制（验证：e2e 恢复会话中权限询问、工具调用、/resume、继续追加均正常；`maybe_compact_on_restore` 单测覆盖压缩与失败路径）

## 编译与测试

- [x] `python -m compileall mewcode/` 无错误
- [x] `python -m pytest tests/ -q` 全部通过（441 passed，含 ch01–ch10 全部既有测试）
- [x] 不引入新第三方依赖（验证：全部标准库实现——json/os/time/random/string/threading/pathlib/datetime/re/collections.abc，无 requirements 改动）

## 端到端场景（tmux）

### 场景 1：记忆闭环（AC3/AC4/AC9/AC10）

1. [x] tmux 中启动 MewCode（新会话，/tmp/e2e-ch11）→ 欢迎面板含「新会话 20260922-205805-imrl。」
2. [x] 输入对话请求（列出目录文件 + 告知偏好）→ run_command/find_files 工具调用与中文回复正常；下一条输入时出现灰色「📝 记忆已更新：1 条新增 / 0 条更新」；期间输入不阻塞（异步）
3. [x] 退出后 `.mewcode/sessions/` 出现 `20260922-205805-imrl.jsonl`：7 行全部合法 JSON、type 序列 = user/assistant/tool_result×2/assistant/user/assistant；`~/.mewcode/memory/` 出现 `chinese-concise-replies.md`（frontmatter 含 name/description/type/updated）与 `index.md`（`- [chinese-concise-replies](chinese-concise-replies.md) — …` 格式，远小于 200 行/25KB）
4. [x] `python -m mewcode --resume` 重启：欢迎面板显示「已恢复会话 20260922-205805-imrl（…，7 条消息）」
5. [x] 提问「我们上次聊到哪了？」→ 模型正确复述上次聊的两件事（历史完整恢复）并提到用户级偏好记忆（索引已注入）
6. [x] `/resume` 列出该会话（标题/ID/消息数 11 条/最后活动时间）；继续对话一轮后退出，JSONL 行数 7 → 13（继续追加同一文件，全部合法）

### 场景 2：恢复异常处理（AC5/AC7/AC8）

1. [x] 构造 40 天前 mtime 的旧会话 + 坏尾会话（未配平 tool_use + 截断在多字节字符中间的半行坏数据）+ 8 天前会话
2. [x] 启动 `--resume <坏尾文件 ID>`：输出「已清理 1 个过期会话存档。」（旧文件确实被删除）、「已恢复会话 …（帮我查个东西，1 条消息）」「发现未写完的对话尾巴，已截断至最后一个完整轮次。」——坏行残尾不再导致崩溃（本次 e2e 修复的 bug）
3. [x] `--resume <8 天前文件 ID>`：输出「该会话已超过 7 天，已插入时间跨度提醒。」；提问「现在几点了」→ 模型查当前时间并回答「上次对话是 8 天前（2026-09-14 21:03）」（时间跨度提醒被正确理解）

### 场景 3：三层指令与 include（AC1/AC2/AC11）

1. [x] 项目根 `MEWCODE.md`（含 `@include team.md` 与越界 `@include /tmp/e2e-outside.md`）+ `.mewcode/instructions.md` + `~/.mewcode/instructions.md` 各写可辨识内容 → 启动输出「警告: include 目标超出允许目录，已跳过: /tmp/e2e-outside.md」
2. [x] 提问「你叫什么名字？团队？喜欢吃什么？」→ 回答「我叫阿黑…团队叫「猫军团」…最爱吃鱼，尤其是秋刀鱼」——三层内容全部注入、项目内 include 正常展开
3. [x] 把 `.mewcode/instructions.md` 写坏（非法 UTF-8）→ 启动仍成功，输出「警告: 指令文件 … 读取失败，已跳过: 'utf-8' codec can't decode…」，TUI 正常进入
