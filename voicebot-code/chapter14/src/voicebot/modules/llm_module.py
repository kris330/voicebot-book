
import asyncio
import logging
from voicebot.event_bus import EventBus
from voicebot.events import (
    ASRResultEvent,
    LLMTokenEvent,
    LLMSentenceEvent,
    LLMEndEvent,
    EventType,
)
from voicebot.tts.text_processor import SentenceSplitter

logger = logging.getLogger(__name__)


class LLMModule:
    """
    LLM 模块

    订阅：ASR_FINAL_RESULT（触发生成）
    发布：LLM_TOKEN、LLM_SENTENCE_READY、LLM_END
    """

    def __init__(self, bus: EventBus, llm_engine) -> None:
        self._bus = bus
        self._llm = llm_engine
        self._splitter = SentenceSplitter()
        self._setup_subscriptions()

    def _setup_subscriptions(self) -> None:
        self._bus.subscribe(
            EventType.ASR_FINAL_RESULT,
            self._handle_asr_result,
            priority=50,
            name="LLMModule.handle_asr",
        )

    async def _handle_asr_result(self, event: ASRResultEvent) -> None:
        """收到 ASR 结果，触发 LLM 生成"""
        logger.info(f"LLM 收到用户输入：{event.text}")

        accumulated = ""
        sentence_buffer = ""
        sentence_seq = 0

        async for token in self._llm.generate_stream(event.text):
            accumulated += token
            sentence_buffer += token

            # 发布 token 事件（用于网关实时显示）
            await self._bus.publish(LLMTokenEvent(
                session_id=event.session_id,
                token=token,
                accumulated_text=accumulated,
            ))

            # 检查是否形成了完整句子
            sentences = self._splitter.split(sentence_buffer)
            if len(sentences) > 1:
                # 除了最后一个（可能不完整），其余都是完整句子
                for sentence in sentences[:-1]:
                    if sentence.strip():
                        await self._bus.publish(LLMSentenceEvent(
                            session_id=event.session_id,
                            sentence=sentence,
                            sequence_number=sentence_seq,
                        ))
                        sentence_seq += 1
                # 保留最后一个（可能不完整的）片段
                sentence_buffer = sentences[-1]

        # 发布剩余的最后一个句子
        if sentence_buffer.strip():
            await self._bus.publish(LLMSentenceEvent(
                session_id=event.session_id,
                sentence=sentence_buffer,
                sequence_number=sentence_seq,
            ))

        # 发布 LLM 结束事件
        await self._bus.publish(LLMEndEvent(
            session_id=event.session_id,
            full_response=accumulated,
        ))
