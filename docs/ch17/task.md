# 记忆渐进式披露（方案 A：召回工具）Tasks

## 文件清单

| 操作 | 文件 | 职责 |
|------|------|------|
| 修改 | `mewcode/memory/prompts.py` | +召回提示模板、请求组装、响应解析、RecallHit/RecallParseError；NOTES 提示文案改新上限 |
| 修改 | `mewcode/memory/notes.py` | 索引上限常量 200 行/25KB → 100 行/12KB |
| 新建 | `mewcode/memory/recall.py` | RecallMemoryTool |
| 修改 | `mewcode/cli.py` | 构造并注册召回工具 |
| 新建 | `tests/test_memory_recall.py` | 提示/解析/工具执行/新上限测试 |

## T1: prompts.py 新增召回提示与解析

**文件：** `mewcode/memory/prompts.py`
**依赖：** 无
**步骤：**
1. 新增 `RecallParseError` 异常类（召回响应解析失败，中文消息）
2. 新增 `RecallHit` dataclass（`scope: str`、`slug: str`）
3. 新增 `RECALL_SYSTEM_PROMPT`：记忆召回器角色——给定 query 与两级索引（每行 `- [name](name.md) — description`），选出与 query 最相关的笔记，最多 3 条；只输出 JSON `{"notes":[{"scope":"user|project","slug":"<name>"}]}`，无相关输出 `{"notes":[]}`，不输出其他文本
4. 新增 `build_recall_request(query, user_index, project_index)`：组装【查询】+【用户级索引】+【项目级索引】小节
5. 新增 `parse_recall_response(text)`：复用 `_extract_json`；解析 notes 列表；scope 非 user/project、slug 非 str 的条目丢弃；按 (scope, slug) 去重；截断到 3 条；整体非法抛 `RecallParseError`
6. `NOTES_SYSTEM_PROMPT` 第 3 条规则「保持 ≤200 行且 ≤25KB」改为「保持 ≤100 行且 ≤12KB」

**验证：** `python -m pytest tests/test_memory_notes.py -q` 现有测试全过（提示文案改动不影响解析逻辑）

## T2: notes.py 索引上限瘦身

**文件：** `mewcode/memory/notes.py`
**依赖：** 无
**步骤：**
1. `INDEX_MAX_LINES` 200 → 100，注释同步
2. `INDEX_MAX_BYTES` 25 * 1024 → 12 * 1024，注释同步

**验证：** `python -m pytest tests/test_memory_notes.py -q`——若存量测试断言旧上限值，同步更新断言后全过

## T3: recall.py 新建 RecallMemoryTool

**文件：** `mewcode/memory/recall.py`
**依赖：** T1
**步骤：**
1. 模块 docstring：渐进式披露方案 A——召回工具，构造注入 provider 与两级记忆目录
2. `RecallMemoryTool(Tool)`：`name="recall_memory"`、`description`（中文：需要了解用户偏好/项目约定/过往经验时调用；query 用自然语言描述要查的内容）、`input_schema`（query 必填 string）、`read_only=True`
3. `__init__(provider, user_dir, project_dir)`：建两个 `NoteManager` 实例
4. `execute(args)`：
   - query 非空校验 → 否则 `failure("query 必须是非空的检索描述")`
   - 读两份 `index_text()`；皆空 → `success("没有找到相关记忆")`（不调 LLM）
   - `build_recall_request` → `_collect()` 同步收集 LLM 回复 → `parse_recall_response`
   - 逐条读 `<slug>.md` 全文（FileNotFound 跳过），拼 `## 笔记: <slug>（用户级/项目级记忆）\n<全文>`；全部缺失 → `success("没有找到相关记忆")`
   - LLM 调用抛 `ProviderError` → `failure("记忆检索失败: <原因>")`
5. `_collect(request)`：`SystemPrompt(stable=RECALL_SYSTEM_PROMPT, dynamic="")` + `provider.stream([Message(role="user", text=request)], [], system)` 收集 TextDelta（同 `NoteUpdater._call_llm` 模式）

**验证：** `python -m compileall mewcode/memory/recall.py` 通过

## T4: cli.py 装配注册

**文件：** `mewcode/cli.py`
**依赖：** T3
**步骤：**
1. 导入 `RecallMemoryTool`、`USER_MEMORY_DIR`、`PROJECT_MEMORY_DIR`
2. 在 `build_memory_context` 之后、`AgentTool` 注册旁：`registry.register(RecallMemoryTool(provider, USER_DIR / USER_MEMORY_DIR, project_root / PROJECT_MEMORY_DIR))`
3. `main` docstring 装配顺序注释补「ch17 召回工具注册」

**验证：** `python -m compileall mewcode/cli.py` 通过；启动冒烟（T6 e2e 一并）

## T5: tests/test_memory_recall.py

**文件：** `tests/test_memory_recall.py`
**依赖：** T1、T2、T3
**步骤：**
1. `build_recall_request`：输出含 query 与两份索引文本
2. `parse_recall_response`：正常 JSON / ```json 围栏剥离 / 非法 JSON 抛 `RecallParseError` / scope 非法条目丢弃 / 超 3 条截断 / 重复条目去重
3. `RecallMemoryTool.execute`（fake provider 参照 `tests/test_memory_notes.py` 写法）：
   - 两份索引皆空 → 「没有找到相关记忆」且 provider 未被调用（计数断言）
   - 正常召回 → 结果含笔记全文与「（用户级/项目级记忆）」来源标注
   - LLM 抛 `ProviderError` → failure 含「记忆检索失败」
   - query 空 → failure
   - 选中 slug 文件缺失 → 跳过不报错
4. `write_index` 新上限：150 行索引写回后 ≤100 行；>12KB 内容写回后 ≤12KB

**验证：** `python -m pytest tests/test_memory_recall.py -q` 全过

## T6: 全量回归 + e2e

**依赖：** T1-T5
**步骤：**
1. `python -m pytest tests/ -q` 全量通过（重点确认 ch11 记忆测试无回归）
2. tmux 启动 MewCode，输入真实请求（如「上次我们记录的仓库卫生问题是什么？」）→ 观察模型调用 `recall_memory` 工具 → 基于取回记忆回答
3. 按 checklist.md 逐项验收

**验证：** 全量测试通过；e2e 观察到工具调用与正确回答

## 执行顺序

```
T1 ─→ T3 ─→ T4 ─→ T6
T2 ─┘          ↑
               T5 ─┘
```
