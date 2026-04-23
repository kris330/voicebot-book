
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from voicebot.session import Session
from voicebot.pipeline import VoicePipeline


class TestInterruption:

    def setup_method(self):
        self.ws = AsyncMock()
        self.ws.send = AsyncMock()
        self.session = Session(self.ws)

    @pytest.mark.asyncio
    async def test_interrupt_cancels_llm_task(self):
        """打断应该取消正在运行的 LLM 任务。"""
        async def slow_llm():
            await asyncio.sleep(100)

        self.session.current_llm_task = asyncio.create_task(slow_llm())
        await self.session.interrupt()

        assert self.session.current_llm_task is None

    @pytest.mark.asyncio
    async def test_interrupt_drains_tts_queue(self):
        """打断应该清空 TTS 队列。"""
        # 往队列放一些音频
        await self.session.tts_queue.put(b"audio_chunk_1")
        await self.session.tts_queue.put(b"audio_chunk_2")
        await self.session.tts_queue.put(b"audio_chunk_3")

        assert self.session.tts_queue.qsize() == 3

        await self.session.interrupt()

        assert self.session.tts_queue.empty()

    @pytest.mark.asyncio
    async def test_interrupt_is_idempotent(self):
        """多次调用 interrupt 应该是安全的（幂等）。"""
        await self.session.interrupt()
        await self.session.interrupt()  # 不应该抛出异常

    @pytest.mark.asyncio
    async def test_interrupt_when_task_already_done(self):
        """任务已经完成时，打断应该正常处理而不报错。"""
        async def quick_task():
            return "done"

        task = asyncio.create_task(quick_task())
        await asyncio.sleep(0)  # 让任务完成
        self.session.current_llm_task = task

        # 不应该抛出异常
        await self.session.interrupt()
        assert self.session.current_llm_task is None

    @pytest.mark.asyncio
    async def test_pipeline_stops_enqueuing_after_interrupt(self):
        """打断后，合成协程不应该继续往队列放数据。"""
        chunks_enqueued = 0
        original_put = self.session.tts_queue.put

        async def count_put(item):
            nonlocal chunks_enqueued
            chunks_enqueued += 1
            await original_put(item)

        self.session.tts_queue.put = count_put

        # 模拟合成过程中被打断
        async def mock_tts_stream(text):
            for i in range(10):
                yield f"chunk_{i}".encode()
                await asyncio.sleep(0.01)

        # 启动合成，然后立刻打断
        asr_mock = AsyncMock()
        asr_mock.transcribe = AsyncMock(return_value="测试文本")

        llm_mock = MagicMock()
        async def mock_generate(messages):
            yield "你好，"
            await asyncio.sleep(0.05)
            yield "今天天气不错。"
        llm_mock.generate_stream = mock_generate

        tts_mock = MagicMock()
        tts_mock.synthesize_stream = mock_tts_stream

        pipeline = VoicePipeline(asr_mock, llm_mock, tts_mock)

        # 启动流水线
        process_task = asyncio.create_task(
            pipeline.process(self.session, b"fake_audio")
        )
        self.session.current_llm_task = process_task

        # 稍等一下，让流水线开始运行
        await asyncio.sleep(0.02)

        # 执行打断
        await self.session.interrupt()

        # 等流水线彻底结束
        try:
            await asyncio.wait_for(process_task, timeout=1.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        # 打断后，队列应该是空的（或只有很少的数据）
        assert self.session.tts_queue.qsize() == 0


class TestInterruptEdgeCases:

    @pytest.mark.asyncio
    async def test_interrupt_while_no_active_task(self):
        """没有活跃任务时打断应该是安全的。"""
        ws = AsyncMock()
        session = Session(ws)
        # current_llm_task 和 current_tts_task 都是 None
        await session.interrupt()  # 不应该报错

    @pytest.mark.asyncio
    async def test_rapid_interrupts(self):
        """快速连续打断不应该造成问题。"""
        ws = AsyncMock()
        session = Session(ws)

        async def long_task():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        for _ in range(5):
            task = asyncio.create_task(long_task())
            session.current_llm_task = task
            await asyncio.sleep(0.01)
            await session.interrupt()

        assert session.current_llm_task is None
