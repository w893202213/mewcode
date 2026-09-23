# ch14 /skill 技能列表命令 Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/skills/manager.py` | +active_names() 只读激活查询 |
| 修改 | `mewcode/commands/builtins.py` | +render_skill_list / make_skill_handler / register_skill_command |
| 修改 | `mewcode/cli.py` | +装配：注册 /skill 命令 |
| 修改 | `tests/test_skills_manager.py` | +active_names 用例 |
| 修改 | `tests/test_command_builtins.py` | +render_skill_list 纯函数用例 |
| 修改 | `tests/test_skills_commands.py` | +注册元数据与技能撞名用例 |

## T1: SkillManager.active_names()

**文件：** `mewcode/skills/manager.py`、`tests/test_skills_manager.py`
**依赖：** 无
**步骤：**
1. manager.py 在「每轮现取的上下文」区前加 `active_names() -> set[str]`：
   `return set(self._active)`，docstring 注明只读、渲染层用
2. test_skills_manager.py 加用例：未激活返回空集；激活 demo 后返回 {"demo"}；
   激活两个返回两个；clear 后返回空集
**验证：** `python -m pytest tests/test_skills_manager.py -q` 全绿

## T2: render_skill_list 纯函数

**文件：** `mewcode/commands/builtins.py`、`tests/test_command_builtins.py`
**依赖：** 无（与 T1 可并行）
**步骤：**
1. builtins.py 顶部加 `from mewcode.skills.model import Skill`，
   在文案格式化区加 `render_skill_list(skills: dict[str, Skill], active: set[str]) -> str`：
   - 空表 → `"没有可用技能。"`
   - 非空 → 首行「可用技能：」；按名排序每行
     `f"  {'★ ' if name in active else '  '}{name:<12} {skills[name].description}"`；
     统计行仅当 `active & set(skills)` 非空：`f"共 {len(skills)} 个技能，{n_active} 个已激活。"`
2. test_command_builtins.py 加用例：空表文案；无激活（无 ★、无统计行）；
   部分激活（★ 位置正确、统计行数字正确）；按名排序；激活名不在表中时统计行正确
   （用 `parse_skill_file` 临时文件构造 Skill，参照 test_skills_manager 的 `_make_skill`）
**验证：** `python -m pytest tests/test_command_builtins.py -q` 全绿

## T3: make_skill_handler 与 register_skill_command

**文件：** `mewcode/commands/builtins.py`、`tests/test_command_builtins.py`、`tests/test_skills_commands.py`
**依赖：** T2（渲染函数）
**步骤：**
1. builtins.py 加 TYPE_CHECKING 导入 `SkillManager`，新增：
   - `make_skill_handler(manager)` → 返回 `cmd_skill(cmd, ui)` 闭包：
     `ui.display(render_skill_list(manager.skills, manager.active_names()))`，返回 None
   - `register_skill_command(registry, manager)`：注册 CommandSpec(
     name="skill", description="列出全部可用技能与激活状态", usage="/skill",
     type=CommandType.LOCAL, handler=make_skill_handler(manager))
2. test_command_builtins.py 加用例：FakeUi 经 dispatch 执行 /skill → displayed 内容
   与 render_skill_list 输出一致
3. test_skills_commands.py 加用例（复用其现有 fixture）：
   - 注册后 registry.resolve("skill") 命中、visible() 含 /skill、type 为 LOCAL
   - 激活名为 "skill" 的技能 → 短命令跳过注册、take_warnings() 有撞名警告、
     内置 /skill 的 handler 不受影响
**验证：** `python -m pytest tests/test_command_builtins.py tests/test_skills_commands.py -q` 全绿

## T4: cli.py 装配

**文件：** `mewcode/cli.py`
**依赖：** T3
**步骤：**
1. import 区加 `register_skill_command`（commands.builtins 行内追加）
2. `skill_manager = SkillManager(discovered.skills)` 之后加
   `register_skill_command(command_registry, skill_manager)`，
   注释：ch14——/skill 先占名，技能短命令撞名自然跳过
3. 更新 main docstring 装配顺序说明（命令注册中心后追加「/skill 注册」）
**验证：** `python -m compileall mewcode/` 通过；`python -m mewcode --config config.example.yaml`
启动无异常（Ctrl+C 退出）

## T5: 全量回归

**文件：** 无（纯验证）
**依赖：** T1–T4
**步骤：**
1. `python -m pytest tests/ -q` 全量（ch12/ch13 现有用例不回退，N2）
2. 有失败则修复后重跑
**验证：** 全量 pytest 通过

## 执行顺序

```
T1 ─┬─→ T3 → T4 → T5
T2 ─┘
```
