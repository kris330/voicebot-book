
import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    AudioChunkEvent,
    ASRResultEvent,
    EventType,
)

logger = logging.getLogger(__name__)


class ASRModule:
    """
    ASR 模块

    订阅：AUDIO_CHUNK_RECEIVED（接收音频）
    发布：ASR_PARTIAL_RESULT、ASR_FINAL_RESULT
    """

    def __init__(self, bus: EventBus, asr_engine) -> None:
        self._bus = bus
        self._asr = asr_engine
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.AUDIO_CHUNK_RECEIVED,
            self._handle_audio_chunk,
            priority=50,
            name="ASRModule.handle_audio",
        )

    async def _handle_audio_chunk(self, event: AudioChunkEvent) -> None:
        """处理音频块，调用 ASR 引擎"""
        result = await self._asr.process_chunk(
            event.audio_bytes,
            event.sample_rate,
        )

        if result is None:
            return

        asr_event = ASRResultEvent(
            session_id=event.session_id,
            text=result.text,
            is_final=result.is_final,
            confidence=result.confidence,
        )

        await self._bus.publish(asr_event)
