# Ctrl+C 双击退出 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。
> 交互验证用 tmux 进行（项目约定）：tmux 启动 mewcode（mock 服务 8989 后台运行），用 `send-keys` 注入按键、`capture-pane` 观察输出。

## 实现完整性

- [x] `DoublePressTracker` 已实现且行为正确（验证：`pytest tests/test_double_press.py -v` 5 passed，覆盖首次按下/窗口内双击/过期重计/reset/3.0s 边界）
- [x] `c-c` key binding、bottom_toolbar、哨兵识别已接入主循环（验证：`python -m compileall mewcode/` 通过；tmux 双击退出冒烟成功）
- [x] 流式中断回滚分支已实现（验证：tmux 流式中断冒烟无 traceback）

## 集成

- [x] 双击链路：key binding → `DoublePressTracker.press()` → `app.exit(哨兵)` → 主循环 `return`（验证：tmux 双击退出，`EXIT=0`，回到 shell 提示符）
- [x] 流式中断链路：KeyboardInterrupt → 回滚 `session.messages` → `tracker.reset()` → 回到输入行（验证：tmux 流式中断后再次单击 Ctrl+C 只提示不退出）

## 编译与测试

- [x] 项目编译无错误（验证：`.venv/bin/python -m compileall mewcode/` 输出成功）
- [x] 所有单元测试通过（验证：`.venv/bin/python -m pytest tests/ -v` 31 passed，含既有 3 个测试文件与新单测）

## 端到端场景

- [x] 场景 1（AC1+AC2）：输入行输入 `hello` 后按一次 Ctrl+C → 输入行仍显示 `❯ hello`，出现「再按一次 Ctrl+C 退出」提示；3 秒内再按一次 Ctrl+C → 程序退出回到 shell，`EXIT=0`
- [x] 场景 2（AC3）：按一次 Ctrl+C 后等待超过 3 秒再按一次 → 程序不退出（进程仍在运行），提示重新出现（重新计数），继续可用
- [x] 场景 3（AC4+AC5）：发送问题、mock 流式回复进行中按一次 Ctrl+C → 回复停止渲染、显示「回复中断」面板、回到输入行、无 traceback；随后按一次 Ctrl+C → 只提示、不退出（计数已重置）
- [x] 场景 4（AC6）：`/exit` 退出正常（「再见！👋」+ 回到 shell）；重新启动后 Ctrl+D 退出正常（「再见！👋」+ 退出码 0）
