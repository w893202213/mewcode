# 结构化系统提示 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] 系统提示含七个固定模块且按 身份→系统约束→任务模式→动作执行→工具使用→语气风格→文本输出 顺序排列，模块间空行分隔（验证：`python -m pytest tests/test_prompt.py -q` 全绿）
- [ ] 三行原文语义保留：系统提示含「MewCode」「终端」「阅读代码」「编写代码」「调试」「先思考再行动」关键表述（验证：同上单测断言）
- [ ] 配置文件填写 `custom_instructions` 时系统提示出现【自定义指令】模块，不填时不出现（验证：单测断言两态）
- [ ] 六个工具的描述均含与全局指令呼应的规则句（「优先用专用工具」「编辑前必先读」类表述）（验证：单测/`grep` 文本检查 + `python -m pytest tests/test_tools.py tests/test_registry.py -q`）
- [ ] 补充块带 `<supplement>` 特殊标签，内部含【环境信息】小节（工作目录/操作系统/当前日期三条）与指令小节（验证：单测断言文本结构）

## 集成

- [ ] Anthropic 请求体：system 字段为块列表——稳定文本块带缓存断点、动态补充块在后且不带断点；最后一个工具带缓存断点（验证：`python -m pytest tests/test_providers.py -q` 请求体断言全绿）
- [ ] OpenAI 请求体：消息列表第一条为 system 角色消息，稳定文本在最前、动态内容接在后（验证：同上 openai 断言全绿）
- [ ] 缓存字段解析：Anthropic 的写缓存/读缓存字段与 OpenAI 的缓存命中字段被解析进用量信息；服务端不返回时不展示、行为不变（验证：`python -m pytest tests/test_providers.py -q`）
- [ ] Plan Mode 注入迁移：`/plan`、`/do` 后会话历史不出现 Plan Mode 的 user 消息；开关指令随每轮请求注入系统通道（验证：`python -m pytest tests/test_plan_mode.py -q` 全绿）
- [ ] 注入节奏：Plan 模式第 1 轮注入完整版、第 2-3 轮精简版、第 4 轮完整版；非 Plan 模式无指令注入（验证：`python -m pytest tests/test_prompt.py tests/test_plan_mode.py -q`）
- [ ] Plan Mode 原有行为不变：`/plan` 后请求体工具列表只含只读工具、副作用工具不执行；`/do` 恢复全部工具（验证：`python -m pytest tests/test_plan_mode.py -q` + e2e 观察）
- [ ] 多轮循环中每轮请求的系统提示稳定部分逐字节一致，仅动态部分变化（验证：`python -m pytest tests/test_agent_loop.py -q` + 检查 mock 请求记录）
- [ ] 配置读取：`custom_instructions` 字段多行文本正确读取，缺省/缺失不报错（验证：`python -m pytest tests/test_config.py -q`）

## 编译与测试

- [ ] 项目编译无错误：`python -m compileall mewcode/` 通过
- [ ] 全部单元测试通过：`python -m pytest -q` 全绿（含新增与更新后的既有测试）

## 端到端场景（tmux 中运行 MewCode）

- [ ] 场景 1（多轮编辑同一文件）：让 MewCode 新建一个小脚本、读回内容、再改一处、再改一处 → 观察：每次修改前都先 read_file；修改用 edit_file 而非 shell 重定向；每轮回复后用量行随轮次出现「缓存命中 X tokens」且数值增长（真实 API 时）
- [ ] 场景 2（跨轮读文件再修改）：第一轮问某文件内容，第二轮让它修改该文件 → 观察：第二轮先 read_file 再 edit_file；两轮行为连续（模型记得上下文）
- [ ] 场景 3（Plan 调研）：`/plan` 后提出涉及多文件改动的需求 → 观察：工具面板只出现只读工具；模型输出调研结论与计划；会话显示区不出现 Plan Mode 注入消息的痕迹；`/do` 后工具恢复可用
- [ ] 场景 4（混合读写操作）：一次请求让 MewCode 读两个文件并执行一条命令 → 观察：工具调用与结果面板正常展示；最终回复结论先行、Markdown 排版、中文
- [ ] 场景 5（自定义指令生效）：config.yaml 添加 `custom_instructions`（如「每次回复末尾加一个 🐾」）重启 MewCode 对话 → 观察：回复行为按自定义指令变化；删除该字段重启后行为恢复
- [ ] 场景 6（回归）：`/help` 列出命令、双击 Ctrl+C 退出、错误 api_key 显示错误面板且不崩溃——既有交互不受影响
- [ ] 场景 7（请求体核对，mock 模式）：用 `scripts/mock_llm.py` 起 mock 服务完成一轮对话后查看请求记录 → 观察：Anthropic 请求含 system 块与 cache_control 断点；OpenAI 请求 messages[0] 为 system 消息

## 定性评估（人工对比，对应 AC9）

用上述场景 1-4 与「无系统提示」的上一版本（git checkout 到 ch05 末尾的提交后另行运行）各走一遍，记录对比感受：

- [ ] 工具选择质量：专用工具（read_file/edit_file/find_files/grep_code）使用率是否明显上升、shell 绕路是否减少
- [ ] 编辑纪律：「编辑前必先读」遵守情况（read_file 是否出现在每次 edit_file 之前）
- [ ] 回复质量：结论先行、简洁程度、Markdown 排版是否改善
- [ ] 成本可观测：多轮任务中缓存命中展示是否随轮次出现并增长（真实 API 时）
