"""线程桥：把 Agent 的异步事件流搬进同步世界，供 TUI 主线程消费。

Agent 内部纯 asyncio（测试可 async for 直接消费）；TUI 是同步
Rich/prompt_toolkit 渲染，经 run_in_background 在后台线程跑事件循环，
事件逐一放入线程安全队列。仅 TUI 使用。
"""

from __future__ import annotations

import asyncio
import queue
import threading

from mewcode.agent.loop import Agent


def _pump(agent: Agent, text: str, q: queue.Queue) -> None:
    """后台协程：消费 Agent 事件流，逐事件搬运进同步队列。"""

    async def go() -> None:
        async for event in agent.run(text):
            q.put(event)

    asyncio.run(go())


def run_in_background(agent: Agent, text: str) -> queue.Queue:
    """启动一轮 Agent Loop 并返回事件队列（阻塞调用前立即返回）。

    队列以 RoundEnd 事件收尾（Agent 保证，N2）；调用方（TUI）负责
    消费直到 RoundEnd。线程为 daemon：进程退出不等待。
    """
    q: queue.Queue = queue.Queue()
    thread = threading.Thread(
        target=_pump, args=(agent, text, q), daemon=True, name="agent-loop"
    )
    thread.start()
    return q
