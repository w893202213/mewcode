# 输入回显移除与思考期计时提示 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性

- [ ] ThinkingIndicator 渲染灰色 `thinking… Ns`，秒数为经过的整秒数，随假时钟推进而增长（验证：`pytest tests/test_thinking_indicator.py` 中计时测试通过）
- [ ] 渲染组装三态正确：静默期仅计时行；思考期计时行+思考文本；回复期仅正文、无计时（验证：`pytest tests/test_thinking_indicator.py` 中状态组装测试通过）
- [ ] `_chat_round` 不再打印用户输入原文与 `▸ 你` 前缀（验证：`pytest tests/test_thinking_indicator.py` 中无回显测试通过）
- [ ] mock 支持 `MOCK_DELAY` 环境变量，且默认启动行为与之前一致（验证：默认启动 curl 流式间隔不变；`MOCK_DELAY=0.5` 启动后间隔明显变慢）

## 集成

- [ ] 流式事件循环的思考增量与正文增量分支均使用新组装函数，计时与思考块、Markdown 回复衔接正确（验证：全量 `pytest tests/` 通过 + 端到端场景 2）
- [ ] 既有功能无回归：命令、会话、错误提示、中断提示、双击 Ctrl+C 退出（验证：全量 `pytest tests/` 通过 + 端到端场景 3、4）
- [ ] 计时刷新期间输入行始终停在终端底部，不与 patch_stdout 输出打架（验证：端到端场景 1 中观察输入行位置）

## 编译与测试

- [ ] `python -m compileall mewcode/` 无语法错误
- [ ] `.venv/bin/python -m pytest tests/ -q` 全量通过

## 端到端场景

- [ ] 场景 1（AC1/AC2/AC3，thinking 关闭）：`MOCK_DELAY=2` 启动 mock → 启动 MewCode 输入问题回车 → 屏幕上问题只出现一次（无 `▸ 你` 回显）；回车后立即出现灰色 `thinking…` 且秒数每秒跳动；2 秒静默后正文开始流式，计时提示消失
- [ ] 场景 2（AC4/AC6，thinking 开启）：同样环境输入问题 → 静默期计时跳动；思考增量流式期间计时行与灰色思考文本并存、不重叠不错位不闪烁；首个正文 token 到达后计时与思考区消失，正文正常流式
- [ ] 场景 3（AC5 错误路径）：model 配置为 `error-model` → 显示红色「请求失败」面板，无残留计时提示，回到输入行可继续对话
- [ ] 场景 4（AC5 中断路径）：流式输出期间按一次 Ctrl+C → 显示黄色「回复中断」面板，无残留计时提示；双击 Ctrl+C → 正常退出 MewCode
