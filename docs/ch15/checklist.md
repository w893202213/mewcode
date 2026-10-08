# Hook 系统 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] hooks 包四个模块（loader/matcher/actions/manager）可导入且导出接口完整（验证：`python -c "import mewcode.hooks"` 无报错，`HookManager`/`HookRule`/`HookDecision`/`load_hooks` 可访问）
- [ ] `Agent(hook_manager=...)` 与 `tui.run(hook_manager=...)` 新增参数缺省为 None，旧调用方签名兼容（验证：全量存量测试通过）

## 行为检查（对应 spec 验收标准）

- [ ] AC1 无 if 规则无条件触发：配置一条仅 event+action 的规则，对应事件发生时动作执行（验证：单元测试；或 tmux 中 startup 事件写标记文件，启动后文件存在）
- [ ] AC2 十个事件时机：round_start/user_message/assistant_message/tool_before/tool_after/round_end 在含工具调用的一轮对话中按顺序触发；session_start/session_end 在会话起止触发；startup/shutdown 在程序起止触发；独立技能运行路径触发轮次/工具级事件（验证：test_hooks_agent.py + test_hooks_manager.py 通过）
- [ ] AC3 四种匹配模式：同一字符串对精确/glob/`!` 反向/`/正则/` 四种模式命中结果符合预期；all 全真才触发、any 有真即触发；点路径参数缺失时条件不命中（验证：`python -m pytest tests/test_hooks_matcher.py -q` 通过）
- [ ] AC4 拦截回灌：tool_before 挂按参数拒绝的规则，工具不执行、模型收到的失败结果含拒绝原因；拦截动作超时同样拒绝该调用（fail-closed）（验证：test_hooks_agent.py 拦截用例通过；tmux 场景 1）
- [ ] AC5 四种动作：command 的 stdin 收到完整事件上下文 JSON、环境变量 MEW_EVENT/MEW_TOOL 正确；inject 文本出现在下一次 LLM 请求且请求后消失；http 发出请求、非 2xx 只记日志不拒绝非拦截事件；subagent 规则通过加载校验（验证：test_hooks_actions.py 通过）
- [ ] AC6 执行控制：run_once 规则两次触发机会只执行一次；background 命令执行期间事件流继续不阻塞；tool_before 配 background 的配置启动报错（验证：test_hooks_manager.py + test_hooks_loader.py 通过）
- [ ] AC7 失败隔离：命令 exit 1 与 HTTP 超时的 Hook 不中断主流程，一轮对话照常完成，日志有失败记录（验证：test_hooks_manager.py 失败隔离用例通过）
- [ ] AC8 集中校验：非法 event、坏正则、tool_before+background 冲突、非正数 timeout 分别导致启动报错（中文、指明规则序号）；hooks 字段缺失/为空时正常启动（验证：test_hooks_loader.py 通过；tmux 场景 3）

## 集成

- [ ] 注入通道闭环：manager 的待注入提示词经 Agent 每轮 build 前取走、包装进系统提示补充块、请求后清空（验证：test_hooks_agent.py inject 用例断言请求的 system.dynamic 含注入文本且不重复）
- [ ] 装配链：cli 加载 hooks → 构造 HookManager → 传入 tui 与 Agent；startup/shutdown/session_start/session_end 实际触发（验证：tmux 场景 2 的标记文件；`python -m mewcode --help` 正常）
- [ ] 与权限体系边界：Hook 拦截发生在权限闸门放行之后，闸门 deny/ask 的调用不触发 tool_before（验证：test_hooks_agent.py 中断言；或 tmux 中 strict 档观察）

## 编译与测试

- [ ] `python -m compileall mewcode/ tests/` 无报错
- [ ] `python -m pytest -q` 全量通过（ch14 存量 593 例 + 新增 hook 用例，无一失败）

## 端到端场景（tmux）

- [ ] 场景 1 拦截并让模型调整：配置 tool_before 规则（tool=run_command、args.command 正则匹配 `rm`、动作=命令 `exit 1` 且 stdout 输出「Hook 已拦截危险删除，请改用安全方式」）；tmux 启动 MewCode，输入「帮我删除 /tmp/mew_test_file」；观察：工具未执行（文件仍存在）、界面显示失败结果含「Hook 已拦截」、模型收到后调整（解释不能删除或改用其他方式），对话正常继续
- [ ] 场景 2 启动与注入：配置 startup 规则（命令写标记文件 /tmp/mew_hook_started）与 round_start 规则（inject 静态文本「每轮开始前先检查 git status」）；tmux 启动 MewCode 后标记文件存在；输入「看看当前项目状态」，观察模型响应体现注入指令（主动执行 git 相关操作）
- [ ] 场景 3 无配置兼容：临时把 hooks 字段移出 config.yaml，tmux 启动 MewCode 正常进入对话界面，行为与 ch14 一致（启动无报错、/help 可用）
