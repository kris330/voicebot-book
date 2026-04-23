
import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class Session:
    # ... 其他代码省略 ...

    async def interrupt(self) -> None:
        """
        执行打断：取消所有任务，清空队列，重置状态。
        这是打断操作的核心方法。
        """
        logger.info(f"[{self.session_id}] 开始执行打断")

        # 1. 标记打断状态（防止任务在取消过程中继续往队列放数据）
        self._interrupted = True

        # 2. 取消所有进行中的任务
        await self.cancel_current_tasks()

        # 3. 清空 TTS 队列
        await self.drain_tts_queue()

        # 4. 清空 ASR 缓冲区
        self.clear_asr_buffer()

        # 5. 重置打断标记（为下一轮做准备）
        self._interrupted = False

        logger.info(f"[{self.session_id}] 打断完成")

    async def cancel_current_tasks(self) -> None:
        """取消当前所有进行中的任务，等待它们真正结束。"""
        tasks_cancelled = []

        if self.current_llm_task and not self.current_llm_task.done():
            self.current_llm_task.cancel()
            tasks_cancelled.append(("LLM", self.current_llm_task))

        if self.current_tts_task and not self.current_tts_task.done():
            self.current_tts_task.cancel()
            tasks_cancelled.append(("TTS", self.current_tts_task))

        # 等待任务真正结束（cancel 只是发信号，不是立刻终止）
        for task_name, task in tasks_cancelled:
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except asyncio.CancelledError:
                logger.debug(f"[{self.session_id}] {task_name} 任务已取消")
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self.session_id}] {task_name} 任务取消超时，强制结束"
                )
            except Exception as e:
                logger.error(
                    f"[{self.session_id}] 等待 {task_name} 任务结束时出错: {e}"
                )

        self.current_llm_task = None
        self.current_tts_task = None
        logger.debug(f"[{self.session_id}] 所有任务已取消")

    async def drain_tts_queue(self) -> None:
        """清空 TTS 队列，丢弃所有未发送的音频。"""
        count = 0
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break

        if count > 0:
            logger.debug(
                f"[{self.session_id}] TTS 队列已清空，丢弃了 {count} 个音频块"
            )
