# ch14 /skill 技能列表命令 Checklist

> 每一项通过运行代码或观察行为来验证，聚焦系统行为。

## 实现完整性
- [x] /skill 命令已注册并可通过 registry.resolve("skill") 命中（验证：单测通过）
- [x] render_skill_list 空表返回「没有可用技能。」（验证：单测通过）
- [x] render_skill_list 有激活时输出 ★ 标记与统计行、无激活时都省略（验证：单测通过）

## 集成
- [x] /skill 经 dispatch 走 handler → ui.display 输出列表（验证：FakeUi 单测通过）
- [x] cli.py 装配后 /skill 生效：启动时已注册，技能短命令撞名自然跳过（验证：撞名单测通过）
- [x] /help 与 Tab 补全包含 /skill（验证：e2e 实测——/help 列表出现 /skill；输入框 Tab 出 /skill 候选）

## 编译与测试
- [x] `python -m compileall mewcode/` 无错误
- [x] `python -m pytest tests/ -q` 全量通过（ch12/ch13 现有用例不回退）

## 端到端场景（tmux）
- [x] 场景 1：启动 MewCode → 输入 /skill → 显示全部技能列表（名称+描述，
      无激活时无 ★、无统计行）
- [x] 场景 2：输入 /commit 激活 commit 技能 → 再输入 /skill → commit 行首出现 ★，
      末尾「共 N 个技能，1 个已激活。」；/clear 后再 /skill → ★ 消失
- [x] 场景 3：会话运行中新建技能文件 → /skill 不含该技能（重启生效）；
      /help 列表包含 /skill
