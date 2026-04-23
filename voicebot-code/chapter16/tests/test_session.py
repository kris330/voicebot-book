
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from voicebot.session import Session
from voicebot.session_manager import SessionManager


class TestSession:

    def setup_method(self):
        """每个测试用例前创建一个 mock websocket 和 Session。"""
        self.ws = AsyncMock()
        self.session = Session(self.ws, system_prompt="你是一个助手")

    def test_session_id_is_unique(self):
        """每个 Session 的 ID 应该是唯一的。"""
        ws2 = AsyncMock()
        session2 = Session(ws2)
        assert self.session.session_id != session2.session_id

    def test_add_messages_builds_history(self):
        """添加消息后对话历史应该正确记录。"""
        self.session.add_user_message("你好")
        self.session.add_assistant_message("你好，有什么可以帮助你的？")

        assert len(self.session.conversation_history) == 2
        assert self.session.conversation_history[0].role == "user"
        assert self.session.conversation_history[1].role == "assistant"

    def test_get_llm_messages_includes_system_prompt(self):
        """获取 LLM 消息时应该包含 system prompt。"""
        self.session.add_user_message("你好")
        messages = self.session.get_llm_messages()

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "你是一个助手"
        assert messages[1]["role"] == "user"

    def test_clear_asr_buffer(self):
        """清空 ASR 缓冲区应该返回累积的音频数据。"""
        self.session.append_asr_audio(b"chunk1")
        self.session.append_asr_audio(b"chunk2")

        audio = self.session.clear_asr_buffer()

        assert audio == b"chunk1chunk2"
        assert self.session.asr_buffer == []

    def test_idle_seconds_increases_over_time(self):
        """idle_seconds 应该随时间增加。"""
        import time
        time.sleep(0.1)
        assert self.session.idle_seconds >= 0.1

    def test_touch_resets_idle_time(self):
        """touch() 应该重置 idle 时间。"""
        import time
        time.sleep(0.1)
        self.session.touch()
        assert self.session.idle_seconds < 0.05

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self):
        """多次调用 close() 应该是安全的。"""
        await self.session.close()
        await self.session.close()  # 不应该抛出异常
        assert self.session.is_closed

    @pytest.mark.asyncio
    async def test_cancel_tasks(self):
        """cancel_current_tasks 应该取消所有进行中的任务。"""
        async def long_running():
            await asyncio.sleep(100)

        self.session.current_llm_task = asyncio.create_task(long_running())
        self.session.current_tts_task = asyncio.create_task(long_running())

        await self.session.cancel_current_tasks()

        assert self.session.current_llm_task is None
        assert self.session.current_tts_task is None

    def test_history_trimming(self):
        """超过最大轮次时应该截断历史。"""
        from voicebot.session import MAX_HISTORY_TURNS

        # 添加超过限制的对话
        for i in range(MAX_HISTORY_TURNS + 5):
            self.session.add_user_message(f"问题 {i}")
            self.session.add_assistant_message(f"回答 {i}")

        max_messages = MAX_HISTORY_TURNS * 2
        assert len(self.session.conversation_history) <= max_messages


class TestSessionManager:

    @pytest.mark.asyncio
    async def test_create_and_remove_session(self):
        """创建和删除 Session 的基本流程。"""
        manager = SessionManager(session_timeout_seconds=60)
        await manager.start()

        ws = AsyncMock()
        session = await manager.create_session(ws)

        assert manager.active_session_count == 1
        assert await manager.get_session(session.session_id) is session

        await manager.remove_session(session.session_id)

        assert manager.active_session_count == 0
        assert await manager.get_session(session.session_id) is None

        await manager.stop()

    @pytest.mark.asyncio
    async def test_cleanup_expired_sessions(self):
        """超时 Session 应该被自动清理。"""
        manager = SessionManager(
            session_timeout_seconds=0,   # 立即超时
            cleanup_interval_seconds=1,
        )
        await manager.start()

        ws = AsyncMock()
        session = await manager.create_session(ws)
        session_id = session.session_id

        # 等待清理任务运行
        await asyncio.sleep(1.5)

        assert await manager.get_session(session_id) is None
        assert manager.active_session_count == 0

        await manager.stop()

    @pytest.mark.asyncio
    async def test_multiple_sessions_are_isolated(self):
        """多个 Session 的状态应该完全隔离。"""
        manager = SessionManager()
        await manager.start()

        ws_a = AsyncMock()
        ws_b = AsyncMock()
        session_a = await manager.create_session(ws_a)
        session_b = await manager.create_session(ws_b)

        session_a.add_user_message("用户A的问题")
        session_b.add_user_message("用户B的问题")

        # 两个 Session 的历史完全独立
        assert len(session_a.conversation_history) == 1
        assert len(session_b.conversation_history) == 1
        assert session_a.conversation_history[0].content == "用户A的问题"
        assert session_b.conversation_history[0].content == "用户B的问题"

        await manager.stop()
