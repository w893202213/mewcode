"""TuiUiController：界面控制接口的 TUI 实现（ch12 F4）。

命令处理函数只依赖 UiController 协议，本类是协议在 Rich + prompt_toolkit
上的落地：聚合 console / session / agent / gate / memory / config，
把界面副作用翻译成真实渲染，并承担 token 累计（/status 数据源）。
/resume 的多步交互（ch11 F3）整体迁入这里，主循环不再特殊拦截。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

from prompt_toolkit.shortcuts import prompt
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from mewcode.agent.loop import Agent
from mewcode.commands.ui import (
    MemoryInfo,
    PermissionInfo,
    ResumeItem,
    SessionInfo,
    StatusInfo,
    TokenStats,
    UiController,
)
from mewcode.config import ProviderConfig
from mewcode.memory import (
    PROJECT_MEMORY_DIR,
    USER_MEMORY_DIR,
    MemoryContext,
)
from mewcode.memory.sessions import (
    TITLE_CHARS,
    SessionLog,
    maybe_add_time_gap_notice,
)
from mewcode.permissions import MODE_LABELS
from mewcode.session import ChatSession
from mewcode.skills.manager import SkillManager

# 提示行灰色样式（与 tui/app.py 计时行口径一致）
_TIMER_STYLE = "grey50"
# 上下文占用估算：4 字符 ≈ 1 token（与 context/estimator.py 无锚点口径一致）
_CHARS_PER_TOKEN = 4


class TuiUiController(UiController):
    """UiController 的 TUI 实现：所有界面副作用与状态查询的聚合点。"""

    def __init__(
        self,
        console: Console,
        session: ChatSession,
        agent: Agent,
        gate,
        memory: MemoryContext | None,
        config: ProviderConfig,
        root: Path,
        run_agent_round: Callable[[str], None],
        invalidate: Callable[[], None],
        skill_manager: SkillManager | None = None,
        run_independent: Callable[[str, str], None] | None = None,
    ) -> None:
        self._console = console
        self._session = session
        self._agent = agent
        self._gate = gate
        self._memory = memory
        self._config = config
        self._root = root
        self._run_agent_round = run_agent_round
        self._invalidate = invalidate
        # ch13：技能管理器（斜杠命令执行技能用）与独立执行回调（app.py 注入）
        self._skill_manager = skill_manager
        self._run_independent = run_independent
        # token 累计（record_usage 在每轮 Agent Loop 结束时由主循环调用）
        self._round_usage: dict[str, int | None] = {}
        self._total_in = 0
        self._total_out = 0

    # ---------- 展示 ----------

    def display(self, text: str) -> None:
        self._console.print(text)

    # ---------- 提示词类 ----------

    def send_user_message(self, text: str) -> None:
        self._run_agent_round(text)

    # ---------- 技能执行（ch13 F6/F7） ----------

    def run_skill(self, name: str, args: str) -> None:
        """执行技能：activate 热更新后按当前模式分流——shared 注入主对话，
        independent 走独立执行回调（app.py 注入的 _run_independent_skill）。"""
        if self._skill_manager is None:
            self.display("技能系统未启用。")
            return
        result = self._skill_manager.activate(name, args)
        if not result.ok:
            self.display(f"技能执行失败: {result.error}")
            return
        warnings = self._skill_manager.take_warnings()
        for warning in warnings:
            self.display(f"警告: {warning}")
        if result.skill.mode.value == "independent":
            if self._run_independent is None:
                self.display("独立执行不可用（当前界面未接入）。")
                return
            self._run_independent(name, args)
        else:
            self.display(f"已激活技能 {name}（共享模式），指令已注入对话。")
            self.send_user_message(result.body)

    # ---------- 模式切换 ----------

    def enter_plan_mode(self) -> None:
        self._agent.enter_plan_mode()
        self.refresh_status()

    def exit_plan_mode(self) -> None:
        self._agent.exit_plan_mode()
        self.refresh_status()

    def cycle_permission_mode(self) -> str:
        new_mode = self._agent.cycle_permission_mode()
        self.refresh_status()
        return MODE_LABELS[new_mode]

    def refresh_status(self) -> None:
        self._invalidate()

    # ---------- 会话生命周期 ----------

    def clear_conversation(self) -> None:
        self._session.clear()
        # ch13 F7：清空对话顺带清掉已激活技能与已注册的技能命令
        if self._skill_manager is not None:
            self._skill_manager.clear()

    def compact_now(self) -> str:
        return self._agent.compact_now()

    # ---------- 多步交互 ----------

    def choose(self, title: str, items: list[str]) -> int | None:
        """面板展示编号列表并读入选择（1 起）；回车/EOF 取消返回 None。"""
        lines = [f"  {i}.   {item}" for i, item in enumerate(items, start=1)]
        self._console.print(
            Panel("\n".join(lines), title=f"[bold]{title}[/bold]", border_style="cyan")
        )
        while True:
            try:
                answer = prompt("选择编号（回车取消）> ")
            except EOFError:
                return None
            answer = answer.strip()
            if not answer:
                return None
            if not answer.isdigit() or not (1 <= int(answer) <= len(items)):
                self._console.print("请输入列表中的编号（回车取消）。")
                continue
            return int(answer)

    def list_sessions(self) -> list[ResumeItem]:
        """最近 5 个会话（ch11 F3 列表口径）。"""
        items: list[ResumeItem] = []
        for s in self._memory.store.list_recent(5):
            mtime = time.strftime("%m-%d %H:%M", time.localtime(s.mtime))
            items.append(
                ResumeItem(
                    id=s.id,
                    title=s.title,
                    message_count=s.message_count,
                    label=f"{s.title}  {s.id}  {s.message_count} 条  {mtime}",
                )
            )
        return items

    def restore_session(self, session_id: str) -> str:
        """恢复会话主体（ch11 F3）：加载、还原、时间提醒、日志换句柄、超限先压。"""
        try:
            result = self._memory.store.load(session_id)
        except FileNotFoundError:
            return f"会话 {session_id} 的存档已不存在。"
        if not result.messages:
            return f"会话 {session_id} 无有效记录，无法恢复。"
        self._memory.session.restore(result.messages)
        title = result.title or "（无标题）"
        lines = [f"已恢复会话 {session_id}（{title}，{len(result.messages)} 条消息）。"]
        if maybe_add_time_gap_notice(self._memory.session.messages, result.last_ts, time.time()):
            lines.append("该会话已超过 7 天，已插入时间跨度提醒。")
        self._memory.log.close()
        self._memory.log = SessionLog(self._memory.store.path(session_id))
        if result.skipped_lines:
            lines.append(f"跳过 {result.skipped_lines} 行无法解析的记录。")
        if result.truncated:
            lines.append("发现未写完的对话尾巴，已截断至最后一个完整轮次。")
        feedback = self._agent.maybe_compact_on_restore()
        if feedback:
            lines.append(feedback)
        return "\n".join(lines)

    # ---------- 状态查询 ----------

    def status_info(self) -> StatusInfo:
        tokens = TokenStats(
            round_in=self._round_usage.get("in") or 0,
            round_out=self._round_usage.get("out") or 0,
            round_cache_hit=self._round_usage.get("cache_hit"),
            round_cache_write=self._round_usage.get("cache_write"),
            total_in=self._total_in,
            total_out=self._total_out,
            message_count=len(self._session.messages),
            context_tokens=self._estimate_context(),
            window=self._config.context_window,
        )
        return StatusInfo(
            config_name=self._config.name,
            model=self._config.model,
            thinking=self._config.thinking,
            permission_label=MODE_LABELS[self._gate.mode],
            plan_mode=self._agent.plan_mode,
            tokens=tokens,
        )

    def session_info(self) -> SessionInfo:
        if self._memory is None:
            return SessionInfo(enabled=False)
        title = ""
        for m in self._session.messages:
            if m.role == "user" and m.text and not m.tool_results:
                title = m.text
                break
        if title:
            title = title[:TITLE_CHARS] + ("…" if len(title) > TITLE_CHARS else "")
        log_path = self._memory.log.path
        mtime = time.strftime("%m-%d %H:%M", time.localtime(log_path.stat().st_mtime))
        return SessionInfo(
            enabled=True,
            session_id=log_path.stem,
            title=title or "（无标题）",
            message_count=len(self._session.messages),
            archive_path=str(log_path),
            last_activity=mtime,
        )

    def memory_info(self) -> MemoryInfo:
        if self._memory is None:
            return MemoryInfo(enabled=False)
        user_dir = Path.home() / ".mewcode"
        files = [
            f"{label}  已加载" if path.is_file() else f"{label}  缺失"
            for path, label in (
                (self._root / "MEWCODE.md", "MEWCODE.md"),
                (self._root / ".mewcode" / "instructions.md", ".mewcode/instructions.md"),
                (user_dir / "instructions.md", "~/.mewcode/instructions.md"),
            )
        ]
        return MemoryInfo(
            enabled=True,
            instruction_files=files,
            project_notes=len(list((self._root / PROJECT_MEMORY_DIR).glob("*.md"))),
            user_notes=len(list((user_dir / USER_MEMORY_DIR).glob("*.md"))),
            session_files=len(list(self._memory.store.directory.glob("*.jsonl"))),
        )

    def permission_info(self) -> PermissionInfo:
        session_rules, permanent_rules = self._gate.rules.counts()
        return PermissionInfo(
            mode_label=MODE_LABELS[self._gate.mode],
            plan_mode=self._agent.plan_mode,
            session_rules=session_rules,
            permanent_rules=permanent_rules,
        )

    # ---------- TUI 内部（非接口方法） ----------

    def record_usage(self, usage: dict[str, int | None]) -> None:
        """轮末记录最近一轮用量并累计（主循环调用，/status 数据源）。"""
        self._round_usage = dict(usage)
        self._total_in += usage.get("in") or 0
        self._total_out += usage.get("out") or 0

    def _estimate_context(self) -> int:
        """上下文占用估算：消息文本与思考的字符折算（展示级，无锚点口径）。"""
        chars = sum(len(m.text) + len(m.thinking) for m in self._session.messages)
        return chars // _CHARS_PER_TOKEN
