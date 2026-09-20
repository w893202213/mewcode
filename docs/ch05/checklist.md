# Agent Loop Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。对应 spec 验收标准见括号。

## 实现完整性

- [ ] 事件流五类事件齐全（文本增量、工具调用开始、工具结果、Token 用量、进度）且以 RoundEnd 收尾（验证：`python -m pytest tests/test_agent_loop.py -q`，事件序列断言全绿）（AC3）
- [ ] 双路收集：流式期间逐片转发与完整落盘一致（验证：`python -m pytest tests/test_collector.py -q` 全绿；test_agent_loop 中「中间轮文本落盘」断言全绿）（AC4）
- [ ] 单工具执行语义不变：parse_error/未知工具/异常/超时/截断路径覆盖（验证：`python -m pytest tests/test_executor.py tests/test_tools.py -q` 全绿）
- [ ] 多工具分批：只读并发重叠、副作用串行、回灌顺序=原始调用顺序（验证：`python -m pytest tests/test_agent_loop.py -q`，分批测试断言全绿）（AC5）
- [ ] 五种停止条件各有测试路径（验证：`python -m pytest tests/test_agent_loop.py -q`，OK/LIMIT/CANCELLED/UNKNOWN_TOOL/ERROR 断言全绿）（AC2）
- [ ] 两家协议 usage 解析（验证：`python -m pytest tests/test_provider_tools.py tests/test_providers.py -q` 全绿）（AC8）
- [ ] Plan Mode：/plan /do 幂等、提示注入、工具收窄为三个只读工具（验证：`python -m pytest tests/test_plan_mode.py -q` 全绿）（AC7）
- [ ] 计划模式下模型调用写工具被拦截：不执行、回灌「计划模式已禁用」失败结果（验证：`python -m pytest tests/test_plan_mode.py::test_plan_mode_blocks_side_effect_tools -q` 通过）（AC7）

## 集成

- [ ] Agent 经线程桥跑在后台，TUI 主线程从队列消费事件且界面不挂起（验证：`python -m pytest tests/test_agent_loop.py -q` 线程桥测试全绿；tmux e2e 全程 UI 响应正常）
- [ ] 命令工具在循环中不弹 y/n 直接执行；确认机制代码已随 ToolRunner 移除、`requires_confirmation` 标记保留（验证：tmux e2e 中模型调用 run_command 时无确认提示；`grep -rn "requires_confirmation" mewcode/tools/` 标记仍在）（AC6）
- [ ] mock 多轮流按请求体中工具结果数正确分轮（验证：T14 的 curl 三轮验证通过）（AC1）
- [ ] 既有交互兼容：/exit /clear /help、双击 Ctrl+C 退出、欢迎语、ProviderError 红色面板（验证：tmux e2e 逐项操作）（AC9）

## 编译与测试

- [ ] 全量单元测试通过（验证：`python -m pytest -q` 全绿）
- [ ] 语法检查通过（验证：`python -m compileall mewcode/ scripts/` 无输出错误）
- [ ] 无 ToolRunner 残留引用（验证：`grep -rn "ToolRunner\|agent.runner" mewcode/ tests/` 无结果）

## 端到端场景

- [ ] 场景 1（多轮自动推进）：tmux 启动 mock（loop-model）+ mewcode，输入「看看项目结构，改一下 README 的版本号，然后跑测试」→ 观察到「第 1 轮/第 2 轮/第 3 轮」进度、每轮工具调用与结果面板、最终回复，全程无需用户再输入（AC1、AC9）
- [ ] 场景 2（Plan Mode 两段式）：输入 `/plan` → 看到模式提示；提问「设计一个登录模块」→ 观察模型只调研（read_file/find_files/grep_code）并输出计划文本，本轮回落后模式保持；输入 `/do` → 切回提示；再输入「开始吧」→ 模型可用全部工具执行（AC7）
- [ ] 场景 3（取消）：输入一个会触发多轮的请求（never-stop-model 或慢速流），循环中按 Ctrl+C → 显示「已取消（已完成步骤保留）」面板并回到输入行；再次提问时模型仍记得已完成步骤（AC2③）
- [ ] 场景 4（token 用量）：loop-model 一轮跑完后，回复下方显示 dim 样式「本轮 token: 输入 X · 输出 Y」且数值与 mock 返回一致（AC8）
- [ ] 场景 5（命令直接执行）：multi-tool-model 或 loop-model 中模型调用 run_command → 不出现 y/n 提示，直接显示命令执行结果面板（AC6）
