
import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    LLMSentenceEvent,
    TTSAudioChunkEvent,
    InterruptEvent,
    EventType,
)

logger = logging.getLogger(__name__)


class TTSModule:
    """
    TTS 模块

    订阅：LLM_SENTENCE_READY（触发合成）、INTERRUPT（停止播放）
    发布：TTS_AUDIO_CHUNK
    """

    def __init__(self, bus: EventBus, tts_manager) -> None:
        self._bus = bus
        self._tts = tts_manager
        self._interrupted = False
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.LLM_SENTENCE_READY,
            self._handle_sentence,
            priority=50,
            name="TTSModule.handle_sentence",
        )
        self._bus.subscribe(
            EventType.INTERRUPT,
            self._handle_interrupt,
            priority=10,  # 高优先级，尽快处理打断
            name="TTSModule.handle_interrupt",
        )

    async def _handle_interrupt(self, event: InterruptEvent) -> None:
        """处理打断事件"""
        logger.info(f"TTS 收到打断信号：{event.reason}")
        self._interrupted = True

    async def _handle_sentence(self, event: LLMSentenceEvent) -> None:
        """收到 LLM 句子，触发 TTS 合成"""
        if self._interrupted:
            logger.info("TTS 已打断，跳过合成")
            return

        async for audio_chunk in self._tts.speak(event.sentence):
            if self._interrupted:
                logger.info("TTS 合成中途被打断")
                break

            await self._bus.publish(TTSAudioChunkEvent(
                session_id=event.session_id,
                audio_bytes=audio_chunk,
                sequence_number=event.sequence_number,
            ))
