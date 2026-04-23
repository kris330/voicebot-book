
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def generate_session_id() -> str:
    """生成全局唯一的 Session ID。"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    short_uuid = str(uuid.uuid4())[:8]
    return f"{timestamp}-{short_uuid}"


@dataclass
class ConversationMessage:
    """对话历史中的单条消息。"""
    role: str          # "user" | "assistant" | "system"
    content: str
    timestamp: datetime = field(default_factory=datetime.now)


class Session:
    """
    表示一次完整的对话会话。

    一个 Session 对应一个 WebSocket 连接，封装了该连接的所有状态：
    对话历史、TTS 队列、VAD 状态、当前进行中的异步任务。
    """

    def __init__(self, websocket, system_prompt: str = "") -> None:
        self.session_id = generate_session_id()
        self.websocket = websocket
        self.created_at = datetime.now()
        self.last_active_at = datetime.now()

        # 对话状态
        self.conversation_history: list[ConversationMessage] = []
        self.system_prompt = system_prompt
        self.asr_buffer: list[bytes] = []       # 当前轮次的 ASR 音频缓冲
        self.vad_state: str = "silent"          # "silent" | "speaking"

        # TTS 音频队列（服务端往里放，发送协程从里取）
        self.tts_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()

        # 当前进行中的异步任务（用于打断时取消）
        self.current_llm_task: Optional[asyncio.Task] = None
        self.current_tts_task: Optional[asyncio.Task] = None

        # 是否已关闭
        self._closed = False

        logger.info(f"[{self.session_id}] Session 已创建")

    def touch(self) -> None:
        """更新最后活跃时间。每次收到用户消息时调用。"""
        self.last_active_at = datetime.now()

    def add_user_message(self, content: str) -> None:
        """添加用户消息到对话历史。"""
        self.touch()
        self.conversation_history.append(
            ConversationMessage(role="user", content=content)
        )
        logger.debug(f"[{self.session_id}] 用户消息: {content[:50]}...")

    def add_assistant_message(self, content: str) -> None:
        """添加 AI 回复到对话历史。"""
        self.conversation_history.append(
            ConversationMessage(role="assistant", content=content)
        )
        logger.debug(f"[{self.session_id}] AI 回复: {content[:50]}...")

    def get_llm_messages(self) -> list[dict]:
        """
        获取格式化的对话历史，供 LLM API 使用。
        返回 OpenAI 格式的消息列表。
        """
        messages = []

        # 加入 system prompt
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        # 加入对话历史
        for msg in self.conversation_history:
            messages.append({"role": msg.role, "content": msg.content})

        return messages

    def clear_asr_buffer(self) -> bytes:
        """清空 ASR 缓冲区并返回积累的音频数据。"""
        if not self.asr_buffer:
            return b""
        audio_data = b"".join(self.asr_buffer)
        self.asr_buffer = []
        return audio_data

    def append_asr_audio(self, audio_chunk: bytes) -> None:
        """向 ASR 缓冲区追加音频数据。"""
        self.asr_buffer.append(audio_chunk)

    async def cancel_current_tasks(self) -> None:
        """
        取消当前正在执行的 LLM 和 TTS 任务。
        用于打断处理和 Session 关闭时的清理。
        """
        tasks_to_cancel = []

        if self.current_llm_task and not self.current_llm_task.done():
            tasks_to_cancel.append(("LLM", self.current_llm_task))

        if self.current_tts_task and not self.current_tts_task.done():
            tasks_to_cancel.append(("TTS", self.current_tts_task))

        for task_name, task in tasks_to_cancel:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"[{self.session_id}] {task_name} 任务已取消")

        self.current_llm_task = None
        self.current_tts_task = None

    async def drain_tts_queue(self) -> None:
        """清空 TTS 队列，丢弃所有待播放的音频。"""
        count = 0
        while not self.tts_queue.empty():
            try:
                self.tts_queue.get_nowait()
                count += 1
            except asyncio.QueueEmpty:
                break
        if count > 0:
            logger.debug(f"[{self.session_id}] 已清空 TTS 队列，丢弃 {count} 个音频块")

    async def close(self) -> None:
        """
        关闭 Session，释放所有资源。
        可以安全地多次调用（幂等）。
        """
        if self._closed:
            return

        self._closed = True
        logger.info(f"[{self.session_id}] 开始关闭 Session...")

        # 取消所有进行中的任务
        await self.cancel_current_tasks()

        # 清空 TTS 队列
        await self.drain_tts_queue()

        # 发送终止信号给 TTS 发送协程（如果还在等待）
        try:
            self.tts_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass

        # 清空对话历史（释放内存）
        self.conversation_history.clear()
        self.asr_buffer.clear()

        duration = (datetime.now() - self.created_at).total_seconds()
        logger.info(
            f"[{self.session_id}] Session 已关闭，"
            f"持续时间: {duration:.1f}s，"
            f"对话轮次: {len(self.conversation_history)}"
        )

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def idle_seconds(self) -> float:
        """距离上次活跃已过去多少秒。"""
        return (datetime.now() - self.last_active_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"Session(id={self.session_id}, "
            f"vad={self.vad_state}, "
            f"turns={len(self.conversation_history)}, "
            f"idle={self.idle_seconds:.0f}s)"
        )
