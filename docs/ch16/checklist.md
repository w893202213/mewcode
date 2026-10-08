# SubAgent 系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] agents 包可导入且导出完整（验证：`python3 -c "import mewcode.agents"` 无报错，`AgentRole`/`discover_agents`/`TaskManager`/`TaskStatus` 可访问）
- [ ] `Agent(interactive=..., allowed_tools=..., read_cache=...)` 新参数缺省值向后兼容（验证：ch15 全量存量测试通过）

## 行为检查（对应 spec 验收标准）

- [ ] AC1 四级加载与容错：四级目录同名角色取最高优先级版本；缺 name、非法 permission 的文件被跳过并警告、其余照常加载（验证：`.venv/bin/python -m pytest tests/test_agents_discover.py tests/test_agents_model.py -q` 通过）
- [ ] AC2 agent 工具校验：非法 type 返回失败含合法取值；角色不存在返回失败含可用角色名；工具列表始终只有一个 agent 工具名（验证：`tests/test_agent_tool.py` 通过）
- [ ] AC3 定义式启动：子对话空白开始、系统提示含角色正文、工具集=白名单再应用防线且无 agent 工具（验证：`tests/test_agents_manager.py` defined 用例通过）
- [ ] AC4 Fork 启动：子对话=父全部消息+任务消息；首请求消息序列与父一致、stable 相同；background=false 仍进后台（验证：`tests/test_agents_manager.py` fork 用例通过）
- [ ] AC5 状态隔离：父/子读缓存实例互不可见；父会话 grant 不影响子裁决；子 token 用量独立回报（验证：`tests/test_files_cache.py`、`tests/test_agents_loop.py`、`tests/test_agents_manager.py` 对应用例通过）
- [ ] AC6 跑到底：ASK 自动拒绝回灌「非交互模式无法人工确认」、模型改用只读工具完成（验证：`tests/test_agents_loop.py` interactive 用例通过）
- [ ] AC7 三层防线：任何子 Agent 工具集无 agent；deny_tools 剔除生效；后台任务工具集无 agent/use_skill（验证：`tests/test_agents_manager.py` 防线用例通过）
- [ ] AC8 后台管理：/tasks 列出状态/耗时/用量；显式后台、60s 超时自动转、/tasks detach 三种方式各验证；后台数达 5 后再启动失败（验证：`tests/test_agents_manager.py`、`tests/test_tasks_command.py` 通过）
- [ ] AC9 结果通知：完成结果注入主历史含角色名与任务标识、自动续一轮汇报（验证：`tests/test_agents_manager.py` 完成队列用例 + tmux 场景 1）
- [ ] AC10 Hook 对接：hook subagent 动作真实启动（role 缺省 general）；子 Agent 内 tool_before Hook 拦截生效（验证：`tests/test_hooks_subagent.py` 通过；子 Agent 内 Hook 生效由共享 HookManager + `tests/test_agents_manager.py` 断言）

## 集成

- [ ] threading.local 链路闭环：loop 的 to_thread 包装注入 → 工具/hook 线程内取到父上下文与读缓存（验证：`tests/test_agent_tool.py` 有上下文用例 + `tests/test_files_cache.py` 通过）
- [ ] 装配链：cli 角色发现 → TaskManager → AgentTool 注册 → tui 轮末 drain 注入+续轮（验证：tmux 场景 1/2；`python3 -m mewcode --help` 正常）
- [ ] 兼容：无角色目录或模型不调 agent 工具时行为与 ch15 一致（验证：全量存量测试 + tmux 场景 3）

## 编译与测试

- [ ] `python3 -m compileall mewcode/ tests/` 无报错
- [ ] `.venv/bin/python -m pytest -q` 全量通过（ch15 存量 664 + 新增 ch16 用例）

## 端到端场景（tmux）

- [ ] 场景 1 定义式委派：项目目录建 `.mewcode/agents/code-reviewer.md`（只读工具白名单）；tmux 启动 MewCode，输入「用 code-reviewer 角色审查最近一次提交」；观察：界面显示 agent 工具调用、子 Agent 执行（工具调用可见或后台完成）、完成结果以「子 Agent 结果」形式出现在主对话、主 Agent 自动汇报审查结论
- [ ] 场景 2 后台与 /tasks：输入一个会跑较久的委派任务（如让子 Agent 检查多个文件），观察：任务进入后台或 60s 自动转后台的提示、`/tasks` 列出任务状态、任务完成后主对话收到结果通知并续轮
- [ ] 场景 3 无角色兼容：临时移走项目级 agents 目录，启动 MewCode 正常进入对话，普通问答与工具调用不受影响（/help 可用、/tasks 显示空表）
