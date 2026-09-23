# ch14 /skill 技能列表命令 Plan

## 架构概览

纯新增展示命令，不改任何既有逻辑，三个改动点：

1. **SkillManager 开一个只读查询口**：新增 `active_names()` 返回当前激活
   技能名集合（`_active` 的键视图），供渲染层读激活状态——领域逻辑零改动。
2. **builtins.py 新增渲染纯函数与注册函数**：`render_skill_list(skills,
   active)` 把「技能表 + 激活集合」渲染成列表文本（可脱离 TUI 单测，
   满足 N3）；`register_skill_command(registry, manager)` 封装 `/skill`
   的 CommandSpec 元数据与 handler 闭包（handler 闭包捕获 manager，
   与 /help 闭包捕获 registry 同模式——方案 A）。
3. **cli.py 装配一行**：skill_manager 创建后调用
   `register_skill_command(command_registry, skill_manager)`。
   此时 /skill 先于任何技能短命令进注册中心，技能名为 "skill" 的技能
   激活时撞名检测自然命中 → 跳过注册并警告（复用 ch13 AC13，无需新代码）。

补全与 /help 无需改动：Tab 补全与 /help 均以 registry.visible() 为数据源，
/skill 注册即自动出现（F4）。

## 核心接口

### SkillManager.active_names() -> set[str]
返回当前激活技能名集合（只读，供渲染层查询激活标记）。实现为
`set(self._active)` 或等价键视图。

### render_skill_list(skills: dict[str, Skill], active: set[str]) -> str
纯函数：技能表 + 激活集合 → 列表文本。
- 空表 → 返回「没有可用技能。」（F3）
- 非空 → 「可用技能：」+ 每行 `  ★ name          描述`（★ 仅激活技能，
  名字按 /help 同款 12 宽对齐）+ 有激活时末尾统计行
  「共 N 个技能，M 个已激活。」

### make_skill_handler(manager: SkillManager) -> Callable[[ParsedCommand, UiController], CommandResult | None]
返回 /skill 命令处理器：调 `ui.display(render_skill_list(manager.skills,
manager.active_names()))`，返回 None（LOCAL 命令惯例）。

### register_skill_command(registry: CommandRegistry, manager: SkillManager) -> None
组装并登记 /skill 的 CommandSpec（name="skill"、LOCAL 类型、description
与 usage 文案）。须在技能管理器创建后调用（cli 装配层）。

## 模块设计

### commands/builtins.py（改）
**职责：** 新增 /skill 的渲染纯函数、handler 工厂与注册函数。
**对外接口：** `render_skill_list`、`make_skill_handler`、`register_skill_command`。
**依赖：** 现有 `CommandRegistry`/`CommandSpec`/`CommandType`；新增
`mewcode.skills.manager.SkillManager` 类型导入（TYPE_CHECKING 即可，handler
闭包 duck typing；`mewcode.skills.model.Skill` 用于类型标注）。
无循环依赖：skills.manager 只依赖 commands.registry，不反向。

### skills/manager.py（改）
**职责：** 增补只读激活查询。
**对外接口：** `active_names() -> set[str]`。
**依赖：** 无新增。

### cli.py（改）
**职责：** 装配——skill_manager 创建后注册 /skill 命令。
**对外接口：** 无新增。
**依赖：** 新增 `from mewcode.commands.builtins import register_skill_command`。

## 模块交互

```
用户输入 "/skill"
  → parser.ParsedCommand
  → dispatch → registry.resolve("skill") 命中 CommandSpec
  → handler(cmd, ui)
      → manager.skills（启动时扫描的生效技能表，只读）
      → manager.active_names()（当前激活集合，实时）
      → render_skill_list() 渲染文本
      → ui.display(text) → TUI 渲染
```

无模型调用、无磁盘读写（N1）。激活/清空走 ch13 既有路径，/skill 只是
每次执行时现取状态，天然实时（F2）。

## 文件组织

```
mewcode/
├── commands/
│   └── builtins.py    — +render_skill_list / make_skill_handler / register_skill_command
├── skills/
│   └── manager.py     — +active_names()
├── cli.py             — +装配一行：register_skill_command(command_registry, skill_manager)
tests/
├── test_command_builtins.py   — +render_skill_list 纯函数用例、/skill handler 用例
├── test_skills_manager.py     — +active_names 用例
└── test_skills_commands.py    — +register_skill_command 注册与撞名用例
```

## 技术决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| /skill 注册位置 | cli.py 装配层（非 register_builtins 内） | skill_manager 在 build_registry 之后才创建，命令需要闭包捕获它；注册先后也保证 /skill 先占名，技能短命令撞名自然跳过 |
| 激活状态查询 | manager 新增 active_names()，不直接读 _active | 保持私有字段封装；渲染层不感知激活表内部结构 |
| handler 数据来源 | 闭包捕获 manager（与 /help 捕获 registry 同款） | 命令处理器签名固定为 (ParsedCommand, UiController)，闭包是既有惯例，不动接口 |
| 渲染输出格式 | 与 /help 同款 12 宽对齐 + ★ 行首标记 | 视觉一致；★ 放行首比放尾更醒目 |
| 注册后不再跑 validate() | 不跑 | 内置 12 条命令名为固定清单、不含 "skill"，注册顺序已保证无冲突；技能撞名由 ch13 既有机制处理 |
| 统计行条件 | 仅当存在激活技能时显示 | spec F1 明确；避免无激活时输出冗余行 |
