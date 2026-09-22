# 上下文管理 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [x] 估算器工作：无锚点按全量字符÷4 估算；锚点后按「锚点值 + 增量字符差分÷4」估算（验证：`python -m pytest tests/test_estimator.py -q` 通过，5 passed）
- [x] 存盘器工作：结果完整写入 `.mewcode/context/` 编号文件；预览含路径与重新读取提示；目录不可写抛中文错误（验证：`python -m pytest tests/test_store.py -q` 通过，5 passed）
- [x] 摘要 Prompt 与解析工作：指令禁止调工具、要求草稿+正式两段、6 固定部分；解析只取正式段、草稿丢弃；缺正式段报错（验证：`python -m pytest tests/test_prompts.py -q` 通过，8 passed）
- [x] 压缩器工作：轻量预防单条/合计存盘替换；重量兜底重构会话；熔断计数（验证：`python -m pytest tests/test_compressor.py -q` 通过，14 passed）
- [x] 配置层工作：`context_window` 缺省 200000；非法值启动报中文 ConfigError（验证：`python -m pytest tests/test_config.py -q` 通过，29 passed）
- [x] 会话层工作：`replace_head` 替换头部、保留尾段（验证：`python -m pytest tests/test_session.py -q` 通过，8 passed）

## 集成

- [x] 工具结果回灌后立刻过轻量预防：超阈值结果在会话里变成预览+路径（AC3/AC4）（验证：`python -m pytest tests/test_context_loop.py -q` 通过，test_prevent_in_loop 断言事件流出现 prevent 事件且消息内容已替换、文件落盘）
- [x] 每轮请求前自动判定：估算超限即触发压缩，压缩后请求正常发出（AC5）（验证：同上，test_auto_summarize_no_tools 断言事件流出现 summarize_start/summarize_done 且 compacted_messages ≥ 1）
- [x] 摘要 LLM 请求不带任何工具（AC6）（验证：同上，断言摘要调用时 tools 为空列表）
- [x] 压缩后会话形态正确：摘要区用户消息原文保留、摘要消息与边界消息就位、保留区 ≥5 条原文不动（AC5）（验证：同上，断言后续请求 messages[0] 为用户原文、含摘要与边界文本）
- [x] Done 后锚点更新：下一轮估算基于真实 usage 而非全量字符（AC2）（验证：同上，Done(usage) 后触发判定依据锚点值；test_estimator.py 锚点用例覆盖）
- [x] 熔断：摘要连续失败 3 次后自动压缩不再发起、出现熔断提示；`/compact` 仍可发起且成功后解除熔断（AC8）（验证：同上，test_breaker_after_three_failures 断言 3 次 summarize_failed + breaker 事件 + 请求照常完成；test_compact_now_manual_success_and_resets_breaker 断言熔断解除）
- [x] `/compact` 无条件执行压缩并返回中文统计反馈（AC7）（验证：同上，compact_now 用例通过；e2e 观察反馈文本）
- [x] TUI 渲染压缩提示：灰色行展示存盘路径/压缩统计/失败/熔断（AC9）（验证：e2e 观察「正在压缩上下文…」「已压缩上下文…」「工具结果较大，已存盘至…」提示行）

## 编译与测试

- [x] `python -m compileall mewcode/ scripts/ tests/` 无错误
- [x] 全量测试通过：`python -m pytest tests/ -q`（368 passed，既有测试不回归 + 新增用例全绿）

## 端到端场景（tmux + mock LLM）

- [x] 场景 1（轻量预防）：对话中工具产生大结果（26000 字符 > 12000 阈值）→ TUI 出现「工具结果较大，已存盘至 …」灰色提示，`.mewcode/context/tool_result_1.txt` 落盘（26000 字节），会话继续正常（AC3）
- [x] 场景 2（自动压缩）：`context_window: 2000` 连续多轮对话逼近窗口 → TUI 依次出现「正在压缩上下文…」与「已压缩上下文：4 条消息生成摘要，1 条用户消息原文保留，估算节省 2755 token」（共 15 次），mock 日志记录 52 次摘要调用（tools 为空），压缩后对话正常继续（AC5/AC9）
- [x] 场景 3（手动压缩）：任意时刻输入 `/compact` → 显示「已压缩上下文：5 条消息生成摘要，1 条用户消息原文保留，估算节省 2765 token。」；`/help` 列出 `/compact`（AC7）
- [x] 场景 4（用户消息保留）：压缩后请求首条消息为用户原文「开始累积历史」（未被摘要改写）；边界消息「…不是原始对话。涉及文件内容、代码细节时请重新读取文件确认…」就位（AC5/N1）（模型重读行为的完整闭环依赖真实 LLM，mock 层已验证消息与提示就位）

## e2e 发现并修复的问题

1. **摘要/边界消息二次压缩被误当用户原文**：role=user 的摘要消息与边界消息在第二次压缩时被 `_is_user_text` 判为用户原文保留，导致每次压缩后旧摘要越积越多（观察到「25 条用户消息原文保留」实际只有 1 条真用户消息）→ 给 Message 加 `summary` 标记修复，新增回归测试 test_second_summarize_drops_old_summary_messages
2. **context 包循环导入**：`context/__init__.py` 包级导出 Compressor → 经 agent.events 反向触发 agent.loop 循环导入（单独运行 test_estimator.py 即触发）→ 包级不再导出 Compressor，调用方从 `mewcode.context.compressor` 子模块导入
